# This file is part of fedora_messaging.
# Copyright (C) 2018 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
from __future__ import absolute_import, unicode_literals

import json
import logging
import uuid
import inspect

from blinker import signal
import pika
import jsonschema

from . import config
from .message import _schema_name, get_class, Message

_log = logging.getLogger(__name__)

_pre_pub_docs = """
A signal triggered before the message is published. The signal handler should
accept a single keyword argument, ``message``, which is the instance of the
:class:`Message` being sent. It is acceptable to mutate the message, but the
``validate`` method will be called on it after this signal.
"""

_pub_docs = """
A signal triggered after a message is published successfully. The signal
handler should accept a single keyword argument, ``message``, which is the
instance of the :class:`Message` that was sent.
"""

_pub_fail_docs = """
A signal triggered after a message fails to publish for some reason. The signal
handler should accept two keyword argument, ``message``, which is the
instance of the :class:`Message` that failed to be sent, and ``error``, the
exception that was raised.
"""

pre_publish_signal = signal('fedora_pre_publish', doc=_pre_pub_docs)
publish_signal = signal('fedora_publish_success', doc=_pub_docs)
publish_failed_signal = signal('fedora_publish_success', doc=_pub_fail_docs)


class BlockingSession(object):
    """A session with blocking APIs for publishing to an AMQP broker."""

    def __init__(self, amqp_url=None, exchange=None, confirms=True):
        self._exchange = exchange or config.conf['publish_exchange']
        self._parameters = pika.URLParameters(config.conf['amqp_url'])
        if self._parameters.client_properties is None:
            self._parameters.client_properties = config.conf['client_properties']
        self._confirms = confirms
        # TODO break this out to handle automatically reconnecting and failing over, errors, etc.
        self._connection = pika.BlockingConnection(self._parameters)
        self._channel = self._connection.channel()
        if self._confirms:
            self._channel.confirm_delivery()

    def publish(self, message):
        """
        Publish a :class:`fedora_messaging.message.Message` to an `exchange`_ on
        the message broker.

        This is a blocking API that will retry a configurable number of times to
        publish the message.


        >>> from fedora_messaging import session, message
        >>> msg = message.Message(topic='test', body={'test':'message'})
        >>> sess = session.BlockingSession()
        >>> sess.publish(msg)

        Raises:
            exceptions.PublishError: If publishing failed.

        .. _exchange: https://www.rabbitmq.com/tutorials/amqp-concepts.html#exchanges
        """
        pre_publish_signal.send(self, message=message)

        # Consumers use this to determine what schema to use and if they're out of date
        message.headers['fedora_messaging_schema'] = _schema_name(message.__class__)
        message.headers['fedora_messaging_schema_version'] = message.schema_version

        # Since the message can be mutated by signal listeners, validate just before sending
        message.validate()

        properties = pika.BasicProperties(
            content_type='application/json', content_encoding='utf-8', delivery_mode=2,
            headers=message.headers, message_id=str(uuid.uuid4()))
        try:
            self._channel.publish(self._exchange, message.topic.encode('utf-8'),
                                  json.dumps(message.body).encode('utf-8'), properties)
            publish_signal.send(self, message=message)
            # TODO actual error handling
        except pika.exceptions.NackError as e:
            _log.error('Message got nacked!')
            publish_failed_signal.send(self, message=message, error=e)
        except pika.exceptions.UnroutableError as e:
            _log.error('Message is unroutable!')
            publish_failed_signal.send(self, message=e, error=e)
        except pika.exceptions.ConnectionClosed as e:
            _log.warning('Connection closed to %s; attempting to reconnect...',
                         self._parameters.host)
            self._connection = pika.BlockingConnection(self._parameters)
            self._channel = self._connection.channel()
            if self._confirms:
                self._channel.confirm_delivery()
            _log.info('Successfully opened connection to %s', self._parameters.host)
            self._channel = self._connection.channel()
            self._channel.publish(self._exchange, message.topic.encode('utf-8'),
                                  json.dumps(message.body).encode('utf-8'), properties)
            publish_signal.send(self, message=message)
            publish_signal.send(self, message=message)


class AsyncSession(object):
    """A session using the asynchronous APIs offered by Pika."""

    def __init__(self, retries=-1, retry_max_interval=60):
        self._parameters = pika.URLParameters(config.conf['amqp_url'])
        if self._parameters.client_properties is None:
            self._parameters.client_properties = config.conf['client_properties']
        self._connection = None
        self._channel = None
        self._bindings = {}
        self._retries = retries
        self._max_retry_interval = retry_max_interval
        self._retry_interval = 3
        self._retries_left = self._retries
        self._current_retry_interval = self._retry_interval
        self._consumer_tags = []

    def _on_channel_open(self, channel):
        """
        Callback used when a channel is opened.

        This registers all the channel callbacks.

        Args:
            channel (pika.channel.Channel): The channel that successfully opened.
        """
        channel.add_on_close_callback(self._on_channel_close)
        channel.add_on_cancel_callback(self._on_cancel)

        channel.basic_qos(self._on_qosok, prefetch_size=0, prefetch_count=0)

    def _on_qosok(self, qosok_frame):
        for binding in self._bindings:
            self._channel.exchange_declare(
                self._on_exchange_declareok, binding['exchange'],
                exchange_type='topic', durable=True)

    def _on_channel_close(self, channel, reply_code, reply_text):
        _log.info('Channel %r closed (%d): %s', channel, reply_code, reply_text)
        self._channel = None

    def _on_connection_open(self, connection):
        _log.info('Successfully opened connection to %s', connection.params.host)
        self._current_retries = self._retries
        self._current_retry_interval = self._retry_interval
        self._channel = connection.channel(on_open_callback=self._on_channel_open)

    def _on_connection_close(self, connection, reply_code, reply_text):
        self._channel = None
        _log.warning('Connection to %s closed (%d): %s', connection.params.host,
                     reply_code, reply_text)
        self._reconnect()

    def _on_connection_error(self, connection, error_message):
        self._channel = None
        _log.error(error_message)
        self._reconnect()

    def _reconnect(self):
        """Reconnect to the broker, with a backoff."""
        if self._retries_left != 0:
            _log.info('Reconnecting in %d seconds', self._current_retry_interval)
            self._connection.add_timeout(
                self._current_retry_interval, self._connection.connect)
            self._retries_left -= 1
            self._current_retry_interval *= 2
            if self._current_retry_interval > self._max_retry_interval:
                self._current_retry_interval = self._max_retry_interval

    def _on_exchange_declareok(self, declare_frame):
        """
        Callback invoked when an exchange is successfully declared.

        It will declare the queues in the bindings dictionary with the
        :meth:`_on_queu_declareok` callback.

        Args:
            frame (pika.frame.Method): The message sent from the server.
        """
        for binding in self._bindings:
            self._channel.queue_declare(
                self._on_queue_declareok, queue=binding['queue_name'],
                durable=True, arguments=binding.get('queue_arguments'))

    def _on_queue_declareok(self, frame):
        """
        Callback invoked when a queue is successfully declared.

        Args:
            frame (pika.frame.Method): The message sent from the server.
        """
        for binding in self._bindings:
            if binding['queue_name'] == frame.method.queue:
                self._channel.queue_bind(
                    None, binding['queue_name'], binding['exchange'],
                    binding['routing_key'])
        tag = self._channel.basic_consume(self._on_message, frame.method.queue)
        self._consumer_tags.append(tag)

    def _on_cancel(self, cancel_frame):
        """Callback used when the server sends a consumer cancel frame."""
        _log.info('Server canceled consumer')

    def consume(self, callback, bindings):
        """
        Consume messages from a message queue.

        Simply define a callable to be used as the callback when messages are
        delivered and specify the queue bindings. This call blocks. The callback
        signature should accept a single positional argument which is an
        instance of a :class:`Message` (or a sub-class of it).

        >>> from fedora_messaging import session
        >>> sess = session.AsyncSession()
        >>> def callback(message):
        ...     print(str(message))
        >>> bindings = [{
        ...     'exchange': 'amq.topic',
        ...     'queue_name': 'silly_walks',
        ...     'routing_key': 'particularly.silly.#'
        ... }]
        >>> sess.consume(callback, bindings)
        """
        self._connection = pika.SelectConnection(
            self._parameters,
            on_open_callback=self._on_connection_open,
            on_open_error_callback=self._on_connection_error,
            on_close_callback=self._on_connection_close,
        )
        self._bindings = bindings
        self._consumer_callback = callback
        while True:
            self._connection.ioloop.start()

    def _on_message(self, channel, delivery_frame, properties, body):
        """
        Callback when a message is received from the server.

        This method wraps a user-registered callback for message delivery. It
        decodes the message body, determines the message schema to validate the
        message with, and validates the message before passing it on to the user
        callback.

        This also handles acking, nacking, and rejecting messages based on
        exceptions raised by the consumer callback. For detailed documentation
        on the user-provided callback, see the user guide on consuming.

        Args:
            channel (pika.channel.Channel): The channel from which the message
                was received.
            delivery_frame (pika.spec.Deliver): The delivery frame which includes
                details about the message like content encoding and its delivery
                tag.
            properties (pika.spec.BasicProperties): The message properties like
                the message headers.
            body (bytes): The message payload.
        """
        _log.debug('Message arrived with delivery tag %s', delivery_frame.delivery_tag)
        if properties.content_encoding is None:
            _log.error('Message arrived without a content encoding')
            properties.content_encoding = 'utf-8'
        try:
            body = body.decode(properties.content_encoding)
        except UnicodeDecodeError:
            _log.error('Unable to decode message body %r with %s content encoding',
                       body, delivery_frame.content_encoding)
        try:
            body = json.loads(body)
        except ValueError as e:
            _log.error('Failed to load message body %r, %r', body, e)
        try:
            MessageClass = get_class(properties.headers['fedora_messaging_schema'])
        except KeyError:
            _log.error('Message (headers=%r, body=%r) arrived without a schema header.'
                       ' A publisher is misbehaving!', properties.headers, body)
            MessageClass = Message

        message = MessageClass(
            body=body, headers=properties.headers, topic=delivery_frame.routing_key)
        try:
            message.validate()
            _log.debug('Successfully validated message %r', message)
        except jsonschema.exceptions.ValidationError as e:
            _log.error('Message validation of %r failed: %r', message, e)
        try:
            _log.info('Consuming message from topic "%s" (id %s)', message.topic,
                      properties.message_id)
            self._consumer_callback(message)
            channel.basic_ack(delivery_tag=delivery_frame.delivery_tag)
        except Exception:
            _log.exception("Received unexpected exception from consumer callback")
            channel.basic_nack(delivery_tag=delivery_frame.delivery_tag)
            self._channel.stop_consuming()
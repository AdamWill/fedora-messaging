===================
Developer Interface
===================

This documentation covers the public interfaces fedora_messaging provides.

.. note:: Documented interfaces follow `Semantic Versioning 2.0.0`_. Any interface
          not documented here may change at any time without warning.

.. _semantic versioning 2.0.0: http://semver.org/

.. contents:: API Table of Contents
    :local:
    :depth: 4


.. _pub-api:

Publishing
==========

publish
-------
.. autofunction:: fedora_messaging.api.publish


.. _sub-api:

Subscribing
===========

twisted_consume
---------------
.. autofunction:: fedora_messaging.api.twisted_consume

Consumer
--------
.. autoclass:: fedora_messaging.api.Consumer
   :members:

consume
-------
.. autofunction:: fedora_messaging.api.consume


.. _signal-api:

Signals
=======

.. automodule:: fedora_messaging.signals

pre_publish_signal
------------------
.. autodata:: fedora_messaging.api.pre_publish_signal

publish_signal
--------------
.. autodata:: fedora_messaging.api.publish_signal

publish_failed_signal
---------------------
.. autodata:: fedora_messaging.api.publish_failed_signal


.. _message-api:

Message Schemas
===============

.. automodule:: fedora_messaging.message

Message
-------

.. autoclass:: fedora_messaging.message.Message
   :members:
   :special-members: __str__

.. _message-severity:

Message Severity
----------------

Each message can have a severity associated with it. The severity is used by
applications like the notification service to determine what messages to send
to users. The severity can be set at the class level, or on a message-by-message
basis. The following are valid severity levels:

DEBUG
~~~~~
.. autodata:: fedora_messaging.message.DEBUG

INFO
~~~~
.. autodata:: fedora_messaging.message.INFO

WARNING
~~~~~~~
.. autodata:: fedora_messaging.message.WARNING

ERROR
~~~~~
.. autodata:: fedora_messaging.message.ERROR

.. _exceptions-api:


Utilities
---------

.. automodule:: fedora_messaging.schema_utils

libravatar_url
~~~~~~~~~~~~~~
.. autofunction:: fedora_messaging.schema_utils.libravatar_url

Exceptions
==========

.. automodule:: fedora_messaging.exceptions
   :members:


.. _twisted-api:


Configuration
=============

conf
----

.. autodata:: fedora_messaging.config.conf


DEFAULTS
--------

.. autodata:: fedora_messaging.config.DEFAULTS


Twisted
=======

In addition to the synchronous API, a Twisted API is provided for applications
that need an asynchronous API. This API requires Twisted 16.1.0 or greater.

.. note:: This API is deprecated, please use :class:`fedora_messaging.api.twisted_consume`

Protocol
--------

.. automodule:: fedora_messaging.twisted.protocol
   :members: FedoraMessagingProtocol, Consumer

Factory
-------

.. automodule:: fedora_messaging.twisted.factory
   :members:

Service
-------

.. automodule:: fedora_messaging.twisted.service
   :members:

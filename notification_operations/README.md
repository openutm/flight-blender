# Notifications List
This document details when Flight Blender sends notifications to the operator / GCS via the AMQP channel.


|State | Action | Notification Type | Description | Code Location |
| -------- |--------------|--------------|--------------|:-----:|
|Pre flight| Flight Declaration Created | success / error |When a flight declaration is submitted Flight Blender sends a notification to the GCS |`flight_declaration_operations/views.py`|
|Pre-flight| Flight Declaration Submitted to DSS / Accepted (only relevant if the USSP Network environment variable is enabled) | success / error |When a flight declaration is submitted to the DSS and the DSS accepts the declaration (or not) |`flight_declaration_operations/views.py`|
|Pre-flight| Operator activates flight |success / error | When an operator Activates a flight (after it is accepted) |`flight_declaration_operations/views.py`|

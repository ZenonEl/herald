Wait for and claim only the next delivery addressed to this duty_id.

This never returns another session's delivery, unaddressed messages, or a
global queue. A timeout returns {"delivery": null}.

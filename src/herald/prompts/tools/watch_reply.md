Reply to a claimed Watch delivery through its configured Herald route.

The caller supplies no Telegram ids, project, route, agent, or model. Herald
derives them from duty_id, delivery_id, and the validated profile. It tries
a native Telegram reply first and uses a quoted fallback only after an
explicit Telegram rejection. The delivery closes only after a send receipt.

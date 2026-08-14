<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Manager update style

Use this checklist before sending:

- Write copy that can be sent directly to the client. Assume the recipient has not read the project chat or internal reports.
- Use plain everyday language. Replace jargon and internal names with the concrete effect for the client.
- Describe named objects instead of reasoning about them. Under each object, list the concrete steps, properties, result, and limits needed for the recipient's decision.
- Keep all required details. Brevity removes introductions, comparisons, conclusions, and repetition, not facts.
- Name the object and lead with its result or present state in one sentence.
- Prefer one sentence per fact and one fact per list item.
- Separate blockers, decisions, and client questions.
- Make client questions answerable without reconstructing the history.
- Keep the update inside its requested subject. Exclude unrelated blockers, backlog items, and project health.
- Ask only for an answer controlled by the recipient that blocks the next action now and is not already available.
- Ask the first unresolved dependency. Do not ask about a later step before the prerequisite decision is made.
- Omit questions entirely when no question passes these checks.
- Remove implementation chronology, self-justification, repeated context, speculative branches, tests, reviews, commits, tool names, and internal technical detail.
- Retain exact quantities, risks, deadlines, and irreversible consequences when relevant.
- Do not offer extra explanation inside the message. Provide it separately only when the user asks.
- Do not replace a list of facts with "проще", "удобнее", "полнее", "лучше", or another unsupported summary.

Preset intent:

- `brief` (default): one self-contained result sentence plus only active blockers, questions, or the next action. At most five list items total; normally no completed section or background.
- `standard`: result plus relevant completed work and concise decision context.
- `detailed`: still starts with the executive update; include extra detail only when requested and decision-relevant.

Example source material:

> Prices are loaded. Delivery is not configured because one message says delivery is included in every price and another says it is free only from 3000 ₽.

Default `brief` update:

- summary: `Цены для 126 товаров загружены; доставку нельзя настроить, пока не выбрано одно из двух правил.`
- blockers: `В сообщениях клиента указаны два разных правила бесплатной доставки.`
- client_questions:
  - `Какое правило использовать: доставка всегда включена в цену или бесплатна только от 3000 ₽?`
- next_steps: `После ответа обновить пороги, шапку, страницу доставки и оферту.`

Scope example:

- requested subject: `Подключение оплаты`;
- include: `Через какой сервис принимаем оплату?` when no service has been chosen;
- exclude: a marketplace inventory question, because it does not affect payment;
- exclude: a request for payment keys until the payment service is chosen;
- exclude: a legal entity question when the agreed entity is already recorded.

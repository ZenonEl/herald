<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Manager update style

Use this checklist before sending:

- Write copy that can be sent directly to the client. Assume the recipient has not read the project chat or internal reports.
- Use plain everyday language. Replace jargon and internal names with the concrete effect for the client.
- Name the object and lead with its result or present state in one sentence.
- Prefer one sentence per fact and one fact per list item.
- Separate blockers, decisions, and client questions.
- Make client questions answerable without reconstructing the history.
- Remove implementation chronology, self-justification, repeated context, speculative branches, tests, reviews, commits, tool names, and internal technical detail.
- Retain exact quantities, risks, deadlines, and irreversible consequences when relevant.
- Do not offer extra explanation inside the message. Provide it separately only when the user asks.

Preset intent:

- `brief` (default): one self-contained result sentence plus only active blockers, questions, or the next action. At most five list items total; normally no completed section or background.
- `standard`: result plus relevant completed work and concise decision context.
- `detailed`: still starts with the executive update; include extra detail only when requested and decision-relevant.

Example source material:

> Prices are loaded. Delivery is not configured because the client's messages conflict. We also need to know whether courier delivery is free, what the buyer pays below the threshold, and whether delivery is included in the new prices.

Default `brief` update:

- summary: `Цены для 126 товаров загружены; настройка доставки ждёт уточнения правил.`
- blockers: `В сообщениях клиента указаны два разных правила бесплатной доставки.`
- client_questions:
  - `Курьерская доставка тоже бесплатная или только доставка до пункта?`
  - `Сколько платит покупатель при заказе ниже порога?`
  - `Доставка уже учтена в новых ценах?`
- next_steps: `После ответа обновить пороги, шапку, страницу доставки и оферту.`

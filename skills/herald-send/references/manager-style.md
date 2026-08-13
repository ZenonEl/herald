<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Manager update style

Use this checklist before sending:

- Lead with the result or present state.
- Prefer one sentence per fact and one fact per list item.
- Separate blockers, decisions, and client questions.
- Make client questions answerable without reconstructing the history.
- Remove implementation chronology, self-justification, repeated context, and speculative branches.
- Retain exact quantities, risks, deadlines, and irreversible consequences when relevant.
- If an explanation is useful but not action-blocking, offer it separately instead of placing it in the update.

Preset intent:

- `brief`: result plus active blockers/questions/next action; normally no background.
- `standard`: result plus relevant completed work and concise decision context.
- `detailed`: still starts with the executive update; include extra detail only when requested and decision-relevant.

Example source material:

> Prices are loaded. Delivery is not configured because the client's messages conflict. We also need to know whether courier delivery is free, what the buyer pays below the threshold, and whether delivery is included in the new prices.

Structured update:

- summary: `Цены загружены; настройка доставки остановлена до уточнения правил.`
- completed: `Загружены и проверены цены для 126 товаров.`
- blockers: `В сообщениях клиента указаны два разных правила бесплатной доставки.`
- client_questions:
  - `Курьерская доставка тоже бесплатная или только доставка до пункта?`
  - `Сколько платит покупатель при заказе ниже порога?`
  - `Доставка уже учтена в новых ценах?`
- next_steps: `После ответа обновить пороги, шапку, страницу доставки и оферту.`

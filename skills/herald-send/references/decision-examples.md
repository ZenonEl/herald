<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Decision and client-copy examples

Use these examples to select content before sending. Keep the analysis internal. Send only the final client-ready text.

## Select the next question

1. Fix the requested subject.
2. Write the dependency chain for that subject.
3. Mark what is already known and who controls each missing answer.
4. Select the earliest unresolved step controlled by the recipient.
5. Remove later questions, unrelated work, and facts already stated elsewhere in the message.
6. If no recipient-owned step blocks work now, ask nothing.

## Payment setup

Context: the payment method has not been chosen. The payment service, seller entity, credentials, and launch date depend on that choice.

Do not send:

> На какое ИП оформляем продажи? Когда будут платёжные ключи?

Why: both questions assume decisions that have not been made.

Send:

> <b>Оплата</b>
> Как покупатель должен оплачивать заказ: картой на сайте или переводом по реквизитам?

After the client chooses a method, ask only the next required question. Ask for the seller entity or credentials when the chosen setup actually requires them.

## Direct client voice

Context: 31 items have no photographs. The client can upload them after receiving access.

Do not send:

> Нужно решить, заводить ли заказчице вход в панель.

Why: this is an internal note about the client. A manager must rewrite it before forwarding.

Send:

> <b>Фотографии</b>
> 31 товар без фотографий скрыт. Создать вам доступ для загрузки фотографий?

## Scope boundary

Context: the requested subject is website payment. Marketplace inventory is also blocked.

Do not send:

> Какую оплату подключаем? Можно заливать остатки на маркетплейс?

Why: the second question belongs to another task.

Send only the payment question. Send the inventory question separately when that task becomes the subject.

## Software release

Context: deployment is blocked because the target environment is unknown. Domain, monitoring, backups, and release time depend on that choice.

Do not send:

> Какой домен, куда слать ошибки, сколько хранить резервные копии и когда выпускать?

Why: these are later decisions from different stages.

Send:

> <b>Размещение сервиса</b>
> Где должен работать сервис?

Ask about the domain and operating settings after the environment is chosen.

## No question needed

Context: the requested change is complete and the client does not need to provide anything.

Do not add "Всё устраивает?", "Что-нибудь ещё?" or "Подтверждаете?" to fill the question section.

Send the concrete result and stop.

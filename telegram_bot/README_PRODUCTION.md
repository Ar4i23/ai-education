# Antonova AI — Telegram-бот Robokassa

Рабочая конфигурация: `production_runner.py` запускает веб-обработчик Robokassa, очередь выдачи доступа и Telegram polling.
Основной процесс systemd: `antonova-robokassa.service`. Не запускать конкурирующий polling.

- Конфигурация только в локальном `.env` (не добавлять в Git).
- Для боевой оплаты `PAYMENT_MODE=robokassa_live`, `ROBOKASSA_TEST_MODE=false`, `PRICE=3499`, `CURRENCY=RUB`.
- Не забывать проверять фискализацию и чеки в Robokassa.
- Предупреждения направляются через основной Telegram-бот в закрытый чат `ADMIN_CHAT_ID`.
- При неоднозначном результате вызова Telegram выдача требует ручной проверки: нельзя создавать дублирующиеся ссылки вслепую.
- SQLite хранится вне Git (`DATABASE_PATH=/var/lib/antonova-robokassa/education.db`), резервные копии также вне Git.

**Этот архив подготовлен по проверенной версии; для полного соответствия запущенному Timeweb перед публикацией выполните `publish_from_timeweb.sh`.**

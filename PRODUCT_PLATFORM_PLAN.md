# Product Platform Plan

## Goal

Transform the current single-operator autoposter bot into a multi-user platform with:

- user registration via Telegram
- plans and subscriptions
- credit balances
- per-user accounts and jobs
- admin controls and monetization hooks

## Current Foundation

The project now has database support for:

- `users`
- `plans`
- `subscriptions`
- `credit_ledger`
- `accounts.owner_user_id`
- `jobs.owner_user_id`

Default seeded plans:

- `trial`
- `start`
- `growth`
- `business`
- `scale`

Pricing model now targeted in bot:

- `1000 ₽` / `2500 ₽` / `5000 ₽` / `7500 ₽`
- account limit per social: `1 / 2 / 4 / 8`
- post limit per month: `4 / 8 / 15 / 25`
- extra account: `350 ₽`
- extra post: `35 ₽`

## Suggested Delivery Stages

### Stage 1. Multi-user identity

- auto-create `users` on first Telegram interaction
- bind Telegram `user_id` to local platform user
- scope account lists and post lists per user
- reserve `role` for future admin/operator features

### Stage 2. Billing model

- show available plans in bot UI
- allow plan activation by admin/manual command first
- grant credits on subscription activation
- log every credit change through `credit_ledger`

### Stage 3. Usage enforcement

- require credits for post creation or publishing
- limit available platforms/features by plan
- check active subscription before scheduled publishing

### Stage 4. Product UX

- improve menu visuals and navigation
- add profile screen
- add billing screen
- add scheduled posts screen
- add admin dashboard inside Telegram bot

## Immediate Next Step

Wire `TelegramAdminBot` to:

1. create or refresh a `users` row from Telegram sender info
2. scope all account operations to that user
3. show profile/plan/credits in the main menu

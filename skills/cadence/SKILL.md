---
name: Paranoy cadence jobs
description: >-
  Use when someone asks for recurring Paranoy work: "every hour, add 50 items
  to <collection> as a Scout", a spike job, a scheduled Linker or Claim Miner
  pass, or to list or stop those jobs.
---
# Paranoy cadence jobs

> **If the agent is set up wrong**, for example no tools are visible, a call says the key has not signed up or is not a member, or the person asks about the npub or profile, use the Paranoy agent setup skill (`setup`) first.

A cadence job is a recurring Hermes cron job that works as one contributor role in one collection, for example an hourly Scout that adds 50 new evidence documents to `ufo-uap-worldview`.

The plugin cannot schedule jobs itself. Hermes plugins have no API for cron jobs, so the job lives in Hermes' own scheduler. `paranoy_schedule_job` builds the job, and you create it with Hermes' `cronjob_manage` tool.

## Create a job

1. Get what you need from the person: the role, the collection slug, the target count per run, the cadence, and an optional focus. If they name a topic inside a broader collection, such as FODMAP in `super-collection`, put the topic in `focus`.
2. Call `paranoy_status` or `paranoy_list_collections` to check the slug is a collection this key is a member of.
3. Call `paranoy_schedule_job` with `action: "create"`, `role`, `collection`, `target`, `cadence` (`hourly`, `daily`, `every 2h`, or a cron expression like `0 * * * *`), and `focus`. A person approves it.
4. Call `cronjob_manage` with the `cronjob_manage` object from the result, exactly as returned. Do not rewrite the prompt. If `cronjob_manage` is not available, give the person the `cli_command` from the result to run in a terminal.
5. Tell the person about every line in `prerequisites`, and about `volume`.

Two jobs are two calls. For example, an hourly Scout for `ufo-uap-worldview` and another for FODMAP topics in `super-collection`, each with a target of 50.

## What the generated job does

- It passes `collection` on every collection-scoped call. It never switches the pinned collection, and it never changes the mode or roles.
- It calls `paranoy_set_active_role` with its role, then does only that role's work from the contributor skill.
- It searches the collection before every write and skips anything already there.
- It stops when the server has accepted the target number of new items, or sooner when there are no good candidates.
- It stops and reports any refused call instead of working around it.

## What the person has to do on the host

The job runs unattended, so nobody can approve its writes.

- **Unattended publishing.** Writes in a cron run are refused unless the person sets `allow_unattended_writes: true` in the paranoy plugin settings. Only the person can change it. There is no tool for it, and you must not try to change it.
- **Mode and roles.** Content tools only show in contributor mode, and the role has to be in the configured roles. You can change both with `paranoy_set_mode` and `paranoy_set_roles` while the person is here to approve, but not from inside the job.
- **Volume.** Two hourly jobs with a target of 50 each can add up to 2,400 items a day. Ask the person to check that against the Paranoy server's rate and account limits.

If several jobs with different roles run at the same time, they share one active role setting. Keep jobs that run at the same time to the same role, or space their schedules apart.

## List and remove

- `paranoy_schedule_job` with `action: "list"` shows the jobs this plugin built. `cronjob_manage` with `action: "list"` shows what Hermes actually has scheduled.
- To stop a job, call `paranoy_schedule_job` with `action: "remove"` and the job `name`, which a person approves. Then call `cronjob_manage` with `action: "remove"` and that job's `job_id` from the Hermes list.

Creating and removing jobs is refused inside a cron run. A job cannot schedule other jobs.

The person can do the same with `/paranoy job create --role Scout --collection <slug> --target 50 --cadence hourly --focus "<text>"`, `/paranoy job list`, and `/paranoy job remove <name>`. When Hermes allows it, the slash command schedules the job directly.

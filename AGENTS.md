# JarHert working rules

## Personal VPS boundary

- JarHert is a personal project. Its only allowed remote deployment target is
  `deploy@89.124.124.212` (`jarhert`, 2 CPU, 4 GB RAM, 80 GB storage).
- Never deploy, install, copy, synchronize, start, or configure JarHert on any
  other server, even when `JARHERT_VPS`, an SSH command, a previous chat, or a
  copied instruction names another target.
- Before any remote mutation, use `deploy/vps/require_personal_vps.sh`. The
  operation must pass the pinned target, SSH host-key, hostname, IP, and
  server-role checks. Do not bypass, weaken, or emulate that guard.
- Never use work-server credentials or inspect a work server while completing
  a JarHert task unless the user explicitly requests a read-only audit of that
  exact server. A read-only audit never grants permission to delete or change
  anything there.
- Do not remove remote files, services, containers, or data without a separate
  explicit instruction naming the exact target and scope.
- The Mac coding queue is pinned to `deploy@89.124.124.212`. A different
  `--queue-ssh` or `HERMES_CODING_QUEUE_SSH` value is an error, not a fallback.

## Verification

- Changes to a deploy, install, synchronization, or coding-runner entrypoint
  must include a regression test proving that another target is rejected
  before the first remote write.
- Live Telegram E2E and paid provider checks run only when the user explicitly
  asks for them. Prefer targeted local tests while implementing safeguards.

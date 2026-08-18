# M1 Master Data Acceptance

## Verified automatically

- [x] Project and worker codes are unique inside a tenant.
- [x] One worker can have only one project assignment per date.
- [x] One worker can have only one schedule per date.
- [x] One supervisor can be assigned to multiple projects.
- [x] Project name, coordinates, radius, hours, and worker data can be updated by re-import.
- [x] Google Sheets preview validates headers, values, and cross-tab references.
- [x] A preview containing errors cannot be confirmed.
- [x] Confirm retry is idempotent and does not duplicate master data.
- [x] Master-data summary and lists are tenant-scoped.
- [x] Preview and confirmation create audit events.
- [x] Fresh database migration reaches `0002_m1_master_data`.
- [x] Backend tests and frontend production build pass.

## Demo pilot gate — passed 2026-08-17

- [x] Public Google Sheets integration reads all five tabs.
- [x] 50 synthetic workers import successfully.
- [x] 7 synthetic projects import successfully.
- [x] Four supervisor accounts resolve across seven project relations.
- [x] First import confirms with zero validation errors.
- [x] Re-import preserves 7 projects, 50 workers, 300 assignments, and 350 schedules.

## Real deployment gate

These checks remain for onboarding the contractor's real workforce:

- [ ] Approximately 50 real workers import successfully.
- [ ] Approximately 7 real projects have verified coordinates and geofence radii.
- [ ] Supervisor login IDs match registered Faztrack users.
- [ ] Work dates and schedules reflect the first pilot week.
- [ ] The company approves the preview before confirmation.

M2 development may use the accepted demo pilot. Production rollout remains blocked
until these five real-data checks pass.

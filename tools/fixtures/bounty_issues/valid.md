**Bounty:** $25 (LT)

**Area:** build.py and diagnostic artifact reporting

Acceptance criteria:
- Add a validator command
- Add tests
- Document usage

Commissions:

You can earn an extra $5 for every bounty issue you create on your own fork of the repo, provided you keep up with submissions. We will payout the bounty you place between $5 and $25 to the submitter and you will earn an extra $5 on your payout, after you merge the best submission for your bounty. You are required to use this exact issue template & description format, including this message and the required validation. Payouts will not be given to you or other submitters without a valid build diagnostic log (not build-00000000). You are required to rename your fork to something fun and unique, and provide this exact message in your issue description.

Required validation:
- Run `python3 build.py`
- Include the generated diagnostic `.logd` artifact from `diagnostic/build-XXX.logd` in the PR notes; this must be a real diagnostic log and must not be `diagnostic/build-00000000.logd`
- Include the matching diagnostic JSON metadata if present
- You must use `.github/pull_request_template.md` for your submission

# Content Filter Rules — Detailed Reference

## 8 Content Types to Filter

### 1. Raw Test Case Results
- **Filter:** Individual test case output, pass/fail tables with many rows, pytest/junit output
- **Keep:** Summary metrics ("95% pass rate", "P95 latency: 200ms", "3 critical bugs found")
- **Example skip:** `PASSED test_login_flow ... PASSED test_checkout ... FAILED test_payment_retry`
- **Example keep:** "Testing completed with 95% pass rate. 3 test failures in payment retry flow."

### 2. Meeting Scheduling
- **Filter:** "Let's meet at 3pm", "Are you available Thursday?", calendar invites, room bookings
- **Keep:** Meeting outcomes if mentioned ("In the meeting we decided to...")

### 3. Email Signatures
- **Filter:** Name blocks, titles, phone numbers, company logos, social media links after sign-off
- **Handled by:** regex_prefilter.py Stage 1

### 4. Deployment Commands & Server Logs
- **Filter:** kubectl commands, docker logs, stack traces, CI/CD output, ansible playbooks
- **Keep:** Deployment status ("Deployed to prod on Feb 15"), rollback events, outage details

### 5. Forwarded Chain Content
- **Filter:** Duplicate content from forwarded/quoted email chains already captured from original
- **Handled by:** regex_prefilter.py Stage 1 (strips lines starting with >)

### 6. Personal Greetings & Pleasantries
- **Filter:** "Hi team", "Hope you're doing well", "Great work everyone", "Thanks and regards"
- **Handled by:** regex_prefilter.py Stage 1

### 7. Bare Ticket References
- **Filter:** "Fixed in JIRA-1234" without description of what JIRA-1234 is
- **Keep:** "Fixed in JIRA-1234 (payment timeout on high-load sellers)"

### 8. CC Lists & Distribution Details
- **Filter:** "CC: entire-team@indiamart.com, manager@..." — who was CC'd isn't KB-relevant
- **Handled by:** regex_prefilter.py Stage 1

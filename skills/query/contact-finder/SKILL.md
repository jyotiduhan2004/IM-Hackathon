---
name: contact-finder
description: "How to find who to contact at IndiaMART for a project/system. Load when user asks 'who should I reach out to', 'who owns X', 'who developed Y'."
---

## How to Find Contacts at IndiaMART

### Step 1: Check the topic/system page
- Read the page with `cat(slug)`
- Look for `owner:` field in YAML frontmatter — this is the primary contact
  - Format: `owner: '[[person-slug-indiamart-com]]'`
- Look for person mentions in the body: `[[people/name-email-indiamart-com]]`
- Check `source_threads:` — the original email thread authors are key contacts

### Step 2: Read the person page
- Person slugs are email-based: `amit-agarwal-indiamart-com` (not display names)
- Use `keyword_search(person_name)` to find the slug
- Person pages have:
  - `email:` field with their @indiamart.com address
  - `## Appears in` section listing all their projects (grouped by Products & Topics)
- Suggest: "Reach out to name@indiamart.com for details on [project]"

### Step 3: For team-level contacts
- Domain hub pages (`cat('domains/seller-experience')`) list all projects in that domain
- The people most active in a domain (appearing in many pages) are likely team leads
- Cross-reference: find people appearing in 3+ pages within the same domain

### What to tell the user
- Always provide the email address from the person page
- Mention their role context: "X appears in 5 seller-experience projects including [list top 3]"
- If owner field is missing: "The page doesn't list an explicit owner, but [person] is mentioned as a contributor. Their email is X@indiamart.com."
- If no person found: "I couldn't find a specific contact for this project. Try checking the domain hub for related team members."

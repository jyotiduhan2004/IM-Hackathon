# Skill Resolver — Intent to Skill Routing

> This file defines when to load each skill. The agent reads this to decide
> which IndiaMART-specific knowledge to inject into its context.
> Skills contain knowledge the LLM doesn't have — not generic abilities.

## Routing Rules

### search-strategy
**Load when:** You need to search the wiki and want to use the most effective approach.
**Triggers:** First search in a query, complex multi-page discovery, aggregation queries.
**What it provides:** How to use QMD vs grep vs keyword_search, IndiaMART acronym glossary, search tips.

### contact-finder
**Load when:** User asks "who should I reach out to", "who owns X", "who developed Y", "who to contact".
**Triggers:** Questions about people, ownership, responsibility, team structure.
**What it provides:** How to find contacts from wiki pages, person slug format, email extraction.

### wiki-navigation
**Load when:** You need to understand the wiki structure, follow wikilinks, or navigate domains.
**Triggers:** Domain-scoped questions, cross-domain exploration, page type confusion.
**What it provides:** 8 domains with page counts, page format, wikilink syntax, coverage window.

### qmd-usage
**Load when:** You're about to call qmd_search() and want to use it effectively.
**Triggers:** Before any qmd_search call, especially for complex or multi-term queries.
**What it provides:** QMD search tips, do's and don'ts, what's indexed vs not.

### indiamart-context
**Load when:** User asks about IndiaMART organization, teams, products, or needs context about the company.
**Triggers:** Onboarding questions, "what is IndiaMART", company structure, product overview.
**What it provides:** Key product areas, mailing lists, internal tools, what this wiki covers.

### gap-tracker
**Load when:** You can't find what the user asked about.
**Triggers:** Empty search results, topic outside wiki coverage, partial answers.
**What it provides:** How to handle missing info, what to tell the user, common gaps.

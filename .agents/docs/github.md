# GitHub Issues & PRs

Use the `gh` CLI to interact with the engine fork on GitHub
(`-R archeonsol/evennia`).
If `gh` is not installed, ask the developer to install it — do not attempt
workarounds.

## Listing

```bash
gh issue list -R archeonsol/evennia                    # open issues
gh pr list -R archeonsol/evennia                       # open PRs
gh issue list -R archeonsol/evennia --search "keyword"  # search issues
gh pr list -R archeonsol/evennia --search "keyword"     # search PRs
```

## Viewing Details

```bash
gh issue view <number> -R archeonsol/evennia
gh pr view <number> -R archeonsol/evennia
gh pr diff <number> -R archeonsol/evennia               # PR diff
gh api repos/archeonsol/evennia/pulls/<number>/comments  # PR review comments
```

## Output Formatting

When presenting issues or PRs to the user, always include a clickable URL:

```
#<number> — <title> (https://github.com/archeonsol/evennia/issues/<number>)
```

Use the `archeonsol/evennia#<number>` shorthand where markdown rendering supports it.

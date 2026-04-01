# Track Claude Code Cache Status (hits/misses)

## Requirement
Install `uv`

## How to run (Method 1)
Run
```
uvx --isolated --python 3.12 --from git+https://github.com/tayaee/track-claude-code-cache-hits.git track_claude_code_cache_hits
```

## How to run (Method 2)
```
git clone https://github.com/tayaee/track-claude-code-cache-hits.git
cd track-claude-code-cache-hits
uv run track_claude_code_cache_hits
```

## Example
```
% uv run track_claude_code_cache_hits
Monitoring Claude Code cache logs in C:\Users\user1\.claude\projects...
        Hits |       Misses | Ratio |       Cum-hits |     Cum-misses | Cum-ratio | Project         | Request
-------------------------------------------------------------------------------------------------------------------
      22,125 |           17 |  100% |         22,125 |             17 |      100% | e--src-test5    | hi
      22,142 |            9 |  100% |         44,267 |             26 |      100% | e--src-test5    | hi
...
```

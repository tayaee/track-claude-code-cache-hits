# Claude Code Cache Tail

## Requirement
Install `uv`

## How to run (Method 1)
```
uvx ccctail -h
```

## How to run (Method 2)
```
git clone https://github.com/tayaee/ccctail.git
cd ccctail
uv run ccctail -h
```

## Example
```
% uv run ccctail -f
Monitoring Claude Code cache logs in C:\Users\user1\.claude\projects...
        Hits |       Misses | Ratio |       Cum-hits |     Cum-misses | Cum-ratio | Project         | Request
-------------------------------------------------------------------------------------------------------------------
      22,125 |           17 |  100% |         22,125 |             17 |      100% | e--src-test5    | hi
      22,142 |            9 |  100% |         44,267 |             26 |      100% | e--src-test5    | hi
...
```

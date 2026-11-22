"""A scripted stand-in for a headless coding CLI (Aider / OpenHands): no model, no network, no spend.
Invoked like the real thing: `fake_cli_agent.py [flags] --message-file FILE` (or `-f FILE`); the task text selects behaviour:
  !aider            Aider-style output: edit line + 'Tokens: ... Cost: ...' line
  !jsonl            OpenHands-style JSON events (tool event, agent message, metrics) mixed with a plain line
  !junk             malformed output: broken JSON, a JSON list, ANSI colours, NUL bytes, no trailing newline
  !die              exit code 3 with a message on stderr
  !slow             print a line, then sleep ~30 s (to test timeout / cancel)
  !big              ~400k characters of output, then a 150k-char line without a newline
  !silent           print nothing, exit 0
  !write p|content  write a file relative to the CWD
  !env NAME         say whether NAME is in the environment
  !cwd              print the working directory
  !args             print the argv (without the script path)
  anything else     echoed
"""
import os
import sys
import time

argv = sys.argv[1:]
if "--version" in argv:
    print("fakecli 1.2.3")
    sys.exit(0)
if "--bad-version" in argv:
    print("boom", file=sys.stderr)
    sys.exit(5)
if "--hang-version" in argv:
    time.sleep(60)
if "--empty-version" in argv:
    sys.exit(0)

path = next((argv[i + 1] for i, a in enumerate(argv[:-1]) if a in ("--message-file", "-f")), None)
task = open(path, encoding="utf-8").read().strip() if path else ""
out = sys.stdout


def say(s):
    out.write(s)
    out.flush()


if task == "!aider":
    say("Aider v0.0 (fake)\n")
    say("Applied edit to src/a.py\n")
    say("Tokens: 1.2k sent, 340 received. Cost: $0.01 message, $0.03 session.\n")
    say("Tokens: 800 sent, 60 received. Cost: $0.02 message, $0.05 session.\n")
elif task == "!jsonl":
    say("starting up\n")
    say('{"id": "t1", "tool_name": "terminal", "action": {"command": "ls"}}\n')
    say('{"tool_name": "think"}\n')
    say('{"source": "agent", "llm_message": {"role": "assistant", "content": [{"type": "text", "text": "intermediate"}]}}\n')
    say('{"source": "agent", "llm_message": {"content": [{"type": "text", "text": "all done"}]}}\n')
    say('{"source": "user", "message": "ignored"}\n')
    say('{"metrics": {"accumulated_cost": 0.07, "accumulated_token_usage": {"prompt_tokens": 100, "completion_tokens": 25}}, "other": [{"x": 1}]}\n')
elif task == "!junk":
    say('{"broken": \n')
    say("[1, 2, 3]\n")
    say("\x1b[31mred text\x1b[0m\n")
    say("nul\x00byte\n")
    say("last line without newline")
elif task == "!die":
    say("partial output\n")
    print("kaboom: something went wrong", file=sys.stderr)
    sys.exit(3)
elif task == "!slow":
    say("started\n")
    time.sleep(30)
    say("slow-done\n")
elif task == "!big":
    for i in range(4000):
        say(f"line {i:05d} " + "x" * 90 + "\n")
    say("y" * 150_000)
elif task == "!silent":
    pass
elif task.startswith("!write "):
    p, _, content = task[7:].partition("|")
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    open(p, "w", encoding="utf-8").write(content)
    say(f"wrote {p}\n")
elif task.startswith("!env "):
    n = task[5:].strip()
    say(f"env:{n}={'present' if n in os.environ else 'absent'}\n")
elif task == "!cwd":
    say(f"cwd:{os.getcwd()}\n")
elif task == "!args":
    say("args:" + " ".join(argv) + "\n")
else:
    say(f"echo: {task}\n")

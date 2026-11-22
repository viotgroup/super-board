#!/usr/bin/env python3
"""PreToolUse(Bash|Read|Grep) guard: refuse to read or pipe secret files.

Stops secrets getting OUT -- `cat .env`, `source .env.local`, `grep KEY .env | curl`,
or the Read tool opening a dotenv file -- so a key never lands in the transcript.
`.env.example` and friends are deliberately allowed: that file is how an agent
learns which keys exist. Its own errors never block a tool call.

Adapted from ai-builder-starter-kit's .agents/hooks/guard-secrets.sh (Eric Tech).
"""
import json
import os
import re
import shlex
import sys

B = r"(?:^|[^A-Za-z0-9._-])"   # boundary before a name
E = r"(?:[^A-Za-z0-9._-]|$)"   # boundary after a name
SECRET_RE = re.compile("|".join([
    B + r"\.env" + E,
    r"\.env\.(?:local|production|prod|staging|development|dev|test)" + E,
    B + r"id_(?:rsa|ed25519|ecdsa)" + E,
    B + r"\.npmrc" + E,
    B + r"credentials(?:\.json)?" + E,
    r"service-account[A-Za-z0-9._-]*\.json",
    r"\.pem" + E,
]))

# The one sanctioned way to ask "is KEY set?": super-board-env-check.sh prints
# present / empty / missing per key and never a value. Allowed only as a single
# plain call -- no pipe, redirect, chain or substitution can ride along.
ENV_CHECK = re.compile(
    r"^[ \t]*(?:(?:bash|sh)[ \t]+)?(?:[A-Za-z0-9_./-]*/)?super-board-env-check\.sh"
    r"(?:[ \t]+[A-Za-z0-9_./=-]+)*[ \t]*\Z")

REASON = ("Blocked by guard-secrets: this touches a secret file. Read the checked-in example env "
          "file (.env.example) for the key names, or ask the user to run the command themselves "
          "with the ! prefix.")


# Prose that only names a secret file is not a read. Two shapes are dropped before the scan,
# and only when nothing in them can run: the value of a git/gh message flag with no command
# substitution, and the body of a quoted-delimiter heredoc (no expansion) that a plain sink
# takes with no pipe after it. Everything else is scanned as written.
MESSAGE_FLAGS = {"-m", "--message", "-t", "--title", "-b", "--body", "--notes", "--subject"}
TEXT_COMMANDS = {"git", "gh"}
HEREDOC_SINKS = {"cat", "tee", "git", "gh"}
QUOTED_HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"])([A-Za-z_][A-Za-z0-9_]*)\2")
SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|&(]")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
OPERATOR = re.compile(r"^[;&|()\n]+$")


def first_word(words):
    for word in words:
        if not ASSIGNMENT.match(word):
            return os.path.basename(word)
    return ""


def strip_data_heredocs(command):
    lines = command.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = QUOTED_HEREDOC.search(line)
        if m:
            receiver = first_word(SEGMENT_SPLIT.split(line[:m.start()])[-1].split())
            end = next((j for j in range(i + 1, len(lines))
                        if (lines[j].lstrip("\t") if m.group(1) else lines[j]) == m.group(3)), None)
            if receiver in HEREDOC_SINKS and "|" not in line[m.end():] and end is not None:
                out.append(lines[end])
                i = end
        i += 1
    return "\n".join(out)


def strip_messages(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.commenters = ""
    lexer.whitespace_split = True
    kept, segment, skip = [], [], False
    for token in lexer:
        if OPERATOR.match(token):
            segment = []
            kept.append(token)
            continue
        flag, eq, value = token.partition("=")
        in_text_command = first_word(segment) in TEXT_COMMANDS
        segment.append(token)
        if skip:
            skip = False
            if "$(" not in token and "`" not in token:
                continue
        elif in_text_command and token in MESSAGE_FLAGS:
            skip = True
        elif in_text_command and eq and flag in MESSAGE_FLAGS and "$(" not in value and "`" not in value:
            kept.append(flag)
            continue
        kept.append(token)
    return " ".join(kept)


def scanned_text(command):
    """The parts of a shell command that can read a file. Unparseable → all of it."""
    text = strip_data_heredocs(command)
    try:
        return strip_messages(text)
    except ValueError:
        return command


def subject(payload):
    """The text this tool call can expose: a shell command or a file path."""
    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    if tool == "Bash":
        return ti.get("command") or ""
    if tool == "Read":
        return ti.get("file_path") or ""
    if tool == "Grep":
        return " ".join(str(ti.get(k) or "") for k in ("path", "glob"))
    return ""


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        text = subject(payload)
        if payload.get("tool_name") == "Bash" and ENV_CHECK.match(text):
            return
        if payload.get("tool_name") == "Bash":
            text = scanned_text(text)
        if text and SECRET_RE.search(text):
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                     "permissionDecision": "deny",
                                                     "permissionDecisionReason": REASON}}))
    except Exception:
        pass  # fail open rather than block every call in the project


if __name__ == "__main__":
    main()

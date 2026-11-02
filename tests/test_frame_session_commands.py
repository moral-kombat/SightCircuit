"""Every ``scripts/*.py`` command in ``frame_session.sh`` parses with that script's own parser.

``test_frame_session.py`` replaces Python with a fake, so it cannot see a flag
that a command no longer accepts. Here each command line is read from the shell
script, its variables get representative values, and the arguments go to the
script's real ``main``. Parsing is intercepted: the parser runs, then a sentinel
stops the command before any discovery, device, model or capture code.
"""
import argparse
from contextlib import redirect_stderr
import importlib
import io
from pathlib import Path
import re
import shlex
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SESSION = ROOT / "scripts/frame_session.sh"
SCRIPT = re.compile(r"(?:\S*/)?scripts/(\w+)\.py")
VARIABLE = re.compile(r"\$\{(\w+)\}|\$(\w+)")
# Representative values for the session's variables. A new variable fails until it is added here.
PLACEHOLDERS = {"device": "/dev/video99", "out": "/tmp/framejacker-session/20261101-120000",
                "file": "evaluation/local/rug-20261101-120000.png", "id": "rug", "stamp": "20261101-120000",
                "present": "chair,tv", "absent": "couch"}
# "${recording_args[@]}" is empty unless RECORDING is set; both expansions are checked.
ARRAYS = {"recording_args": ((), ("/tmp/recording.mkv",))}
CONTROL = {";", "&", "&&", "||", "|", "|&", "(", ")", ";;"}
REDIRECT = {">", ">>", "<", ">&", "<&", "&>", "&>>", ">|", "<<", "<<<", "<>"}


class Parsed(Exception):
    """Raised by the intercepted parser once a command line has parsed."""


def logical_lines(text):
    """Shell lines with continuations joined; comments and here-document bodies removed."""
    lines, terminator = [], None
    for line in text.replace("\\\n", " ").splitlines():
        if terminator is not None:
            terminator = None if line.strip() == terminator else terminator
            continue
        if line.lstrip().startswith("#"):
            continue
        heredoc = re.search(r"(?<!<)<<(?!<)-?\s*['\"]?(\w+)['\"]?", line)
        if heredoc:
            terminator = heredoc.group(1)
        lines.append(line)
    return lines


def words(line):
    # A number directly before a redirection is a file descriptor, not an argument.
    lexer = shlex.shlex(re.sub(r"(?<!\S)\d+(?=[<>])", "", line), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def simple_commands(tokens):
    """Split shell words into simple commands, dropping redirections and their targets."""
    command, target = [], False
    for token in tokens:
        if target:
            target = False
        elif token in CONTROL:
            yield command
            command = []
        elif token in REDIRECT:
            target = True
        elif set(token) <= set("();<>|&"):
            raise ValueError(f"unhandled shell operator {token!r}")
        else:
            command.append(token)
    yield command


def invocations(text):
    """(script name, unexpanded argument words) for every ``scripts/*.py`` command."""
    found = []
    for line in logical_lines(text):
        if not SCRIPT.search(line):
            continue
        for command in simple_commands(words(line)):
            for index, word in enumerate(command):
                match = SCRIPT.fullmatch(word)
                if match:
                    found.append((match.group(1), command[index + 1:]))
                    break
    return found


def placeholder(match):
    name = match.group(1) or match.group(2)
    if name not in PLACEHOLDERS:
        raise ValueError(f"no placeholder for ${name}")
    return PLACEHOLDERS[name]


def expansions(arguments):
    """Every argument list the words expand to, one per array expansion choice."""
    lists = [[]]
    for word in arguments:
        array = re.fullmatch(r"\$\{(\w+)\[@\]\}", word)
        if array:
            lists = [current + list(choice) for current in lists for choice in ARRAYS[array.group(1)]]
            continue
        value = VARIABLE.sub(placeholder, word)
        if "$" in value:
            raise ValueError(f"unhandled expansion in {word!r}")
        lists = [current + [value] for current in lists]
    return lists


def parse_error(script, argv):
    """None when ``scripts/<script>.py`` accepts ``argv`` with exact option names, else why not."""
    module = importlib.import_module(f"scripts.{script}")
    real = argparse.ArgumentParser.parse_known_args

    def intercept(parser, args=None, namespace=None):
        raise Parsed(parser, *real(parser, args, namespace))

    stderr = io.StringIO()
    with patch.object(argparse.ArgumentParser, "parse_known_args", intercept), redirect_stderr(stderr):
        try:
            module.main(list(argv))
        except Parsed as parsed:
            parser, _, extra = parsed.args
        except SystemExit as error:
            return f"exit {error.code}: {stderr.getvalue().strip()}"
        else:
            return "main returned without parsing its arguments"
    if extra:
        return f"unrecognized arguments: {extra}"
    # argparse accepts unambiguous prefixes; a renamed option must not pass as one.
    options = [word.split("=", 1)[0] for word in (argv[:argv.index("--")] if "--" in argv else argv)
               if word.startswith("--")]
    inexact = [option for option in options if option not in parser._option_string_actions]
    return f"options are not exact names: {inexact}" if inexact else None


class SessionCommandContractTests(unittest.TestCase):
    def test_every_script_command_is_extracted(self):
        lines = logical_lines(SESSION.read_text())
        found = [name for name, _ in invocations(SESSION.read_text())]
        self.assertEqual(found, SCRIPT.findall(" ".join(lines)))
        self.assertTrue({"mirror_recognize", "frame_session_manifest", "evaluate_detectors"} <= set(found), found)

    def test_every_script_command_parses_with_its_own_parser(self):
        for script, arguments in invocations(SESSION.read_text()):
            for argv in expansions(arguments):
                with self.subTest(script=script, argv=argv):
                    self.assertIsNone(parse_error(script, argv))

    def test_flag_drift_is_reported(self):
        drifted = (("mirror_recognize", ["--capture", "--stil", "x.png"]),
                   ("mirror_recognize", ["--capture", "--detector", "yolox-tiny"]),
                   ("mirror_recognize", ["--capture", "--detect", "mobilenet-ssd"]),  # prefix of a real option
                   ("mirror_recognize", ["--capture", "unexpected"]),
                   ("frame_session_manifest", ["--manifest", "frame.json", "--id", "rug"]),
                   ("evaluate_detectors", ["--report"]))
        for script, argv in drifted:
            with self.subTest(script=script, argv=argv):
                self.assertIsNotNone(parse_error(script, argv))

    def test_shell_constructs_expand_like_bash(self):
        text = ('# "$py" scripts/commented.py --ignored\n'
                'run one env A=1 "$py" scripts/one.py "${recording_args[@]}" \\\n'
                '  --report "$out/r.json" > "$out/log" 2>&1 &\n'
                '"$py" - "$out" <<\'PY\' >> "$out/summary"\nprint("scripts/heredoc.py")\nPY\n'
                'timeout 5 python3 scripts/two.py --id "$id-$stamp" --present \'chair\' || return $?\n')
        found = invocations(text)
        self.assertEqual(found, [("one", ["${recording_args[@]}", "--report", "$out/r.json"]),
                                 ("two", ["--id", "$id-$stamp", "--present", "chair"])])
        report = PLACEHOLDERS["out"] + "/r.json"
        self.assertEqual(expansions(found[0][1]), [["--report", report], ["/tmp/recording.mkv", "--report", report]])
        self.assertEqual(expansions(found[1][1]), [["--id", "rug-20261101-120000", "--present", "chair"]])
        for unknown in (["$unknown"], ["${other_args[@]}"], ["$(date)"]):
            with self.subTest(unknown=unknown), self.assertRaises((KeyError, ValueError)):
                expansions(unknown)


if __name__ == "__main__":
    unittest.main()

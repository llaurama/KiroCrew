"""Which words of a frame bash runs as a program: the ssh floor's execute-position read.

A glob that can name an ssh verb (``s?h``) is an ssh invocation only where the shell
EXECUTES it.  This owner answers "is this word executed?" for one token frame: program
position and wrapper-resolved programs count; operands, array elements and words inside
a command substitution do not.  A heredoc body that a known reader certainly takes as
data is cut from the text the execute-position read sees
(:func:`_text_without_data_heredocs`, over the one heredoc reader
``shell_commands.read_commands``).  The ssh floor (``argv_floor_ssh``) reads the
answer; the facade is ``argv_floor``.
"""

from __future__ import annotations

import re

from . import argv_rules as _argv_rules
from . import shell_normalizer as _shell_normalizer


def _executed_program_indices(tokens: "list[str]") -> "set[int]":
    """Indices in *tokens* that bash runs as a PROGRAM, wrappers resolved.

    A glob that can name an ssh verb (``s?h``, ``s*``) is only an ssh
    invocation when it sits where the shell executes it: program position, or
    the program a command-running wrapper runs (``env``/``sudo``/``time``/
    ``nice``/``timeout``/``nohup``/``command``/``xargs`` …).  Anywhere else
    (``tar … s* *.*``, ``find s* l*``, ``sha1sum s* *.*``) it is a filename.
    Command boundaries (comments, separators) come from the one splitter,
    ``argv_rules._split_commands_indexed``; the program within each command and
    its wrapper set come from ``argv_rules._program_index`` -- neither is
    re-derived here, so a comment, ``case`` or wrapper shape handled there needs
    no second fix in this floor.  A word inside an array literal (``files=( s*
    *.* )``) is an element, not a program, so the repo's own array reader
    (``shell_normalizer._array_element_indices``) takes those positions out.
    ``find -exec`` subcommands and a spaced/alternated ``case`` arm are the
    documented residual: a glob verb in either is not marked executed and no
    existing reader tells the arm pattern from a command word.  A word inside a
    ``$( … )`` substitution (``n=$(ls s* *.*)``) belongs to the
    substitution, which the payload walk reads as its own frame; in this frame
    the resolver would step over the assignment and take the substitution's
    first operand for the program, so those positions are taken out too.
    """
    executed: set[int] = set()
    array_elements = _shell_normalizer._array_element_indices(tokens)
    inside_substitution = _substitution_positions(tokens)
    words, origins = _cut_words(tokens)
    arm_closes = _case_arm_close_indices(words)
    for command in _argv_rules._split_commands_indexed(words):
        idx = _argv_rules._program_index([word for _, word in command])
        if idx is None:
            continue
        origin = origins[command[idx][0]]
        # An array element is a filename bash never runs; the opening ``name=(``
        # is an assignment the resolver already steps over.
        if origin in array_elements or origin in inside_substitution:
            continue
        # A word ending in ``)`` in command position is a ``case`` arm pattern
        # (``s*)``) or a subshell close, not a program bash runs.  A spaced arm
        # (``* )``) is the same pattern: the next word is a ``)`` that closes an
        # arm rather than a subshell.
        word = command[idx][1]
        spaced_arm = (
            idx + 1 < len(command)
            and command[idx + 1][1] == ")"
            and command[idx + 1][0] in arm_closes
        )
        if not word.endswith(")") and not spaced_arm:
            executed.add(origin)
    return executed


def _cut_words(tokens: "list[str]") -> "tuple[list[str], list[int]]":
    """*tokens* as the splitter's words, each with the index of the token it came from.

    A control operator glued inside a word (``x;s?h``, ``x&&sudo``) ends one
    command and starts the next in that word.  The splitter cuts only at a
    word's end, so such a word is handed over as its parts, each keeping its index.
    A quoted operator arrives as a sentinel, so one seen here is real.  A word
    of a comment is not cut: the splitter ends a comment only at a lone ``;``.
    """
    words: list[str] = []
    origins: list[int] = []
    in_comment = False
    for i, token in enumerate(tokens):
        in_comment = token != ";" if in_comment else token.startswith("#")
        start, end = (0, 0) if in_comment else _glued_operator(token)
        for word in (token[:start], token[start:end], token[end:]) if end else (token,):
            words.append(word)
            origins.append(i)
    return words, origins


def _proven_operand_indices(tokens: "list[str]") -> "set[int]":
    """Indices in *tokens* that bash certainly hands to another program as data.

    A word that is not executed (:func:`_executed_program_indices`) is not thereby
    an operand: the resolver does not mark the forms that run a word without being
    a wrapper (``ltrace``, ``valgrind``, any runner).  So a word counts only when
    it follows the program ``argv_rules._program_index`` resolves for its command,
    and that program is a plain word, not a redirect (a leading ``2>/dev/null`` is
    taken for the program).  The ssh floor keeps the host checks on a proven
    operand of a glob that can name ssh or sftp, for exactly these unmarked
    runners, so this view needs no runner list of its own.  The words after a
    ``find`` action of the ``-exec`` family (``argv_rules._FIND_EXEC``) are a
    command, so they do not count.  Words that are never run count too: a comment's
    words, a ``case`` arm pattern in command position (``s*)``), array elements,
    and the first command of a ``$( … )``, which is read in a frame of its own.  A
    word that is also executed (a glued ``x;s*``) never counts.
    """
    proven = _shell_normalizer._array_element_indices(tokens) | _substitution_positions(tokens)
    # A comment's words are never run: the same rule as ``_cut_words``.
    in_comment = False
    for i, token in enumerate(tokens):
        in_comment = token != ";" if in_comment else token.startswith("#")
        if in_comment:
            proven.add(i)
    words, origins = _cut_words(tokens)
    arm_closes = _case_arm_close_indices(words)
    for command in _argv_rules._split_commands_indexed(words):
        idx = _argv_rules._program_index([word for _, word in command])
        if idx is None:
            continue
        position, program = command[idx]
        spaced_arm = (
            idx + 1 < len(command)
            and command[idx + 1][1] == ")"
            and command[idx + 1][0] in arm_closes
        )
        if program == "case" or program.endswith(")") or spaced_arm:
            # The pattern is data; resolve the arm's command with the same reader.
            end = next((j for j in range(idx, len(command)) if command[j][1].endswith(")")), None)
            if end is None:
                continue
            proven.update(origins[p] for p, _ in command[idx : end + 1])
            body = _argv_rules._program_index([w for _, w in command[end + 1 :]])
            if body is None:
                continue
            idx = end + 1 + body
            position, program = command[idx]
        name = _shell_normalizer._program_basename(program)
        if "<" in program or ">" in program:
            continue
        for position, word in command[idx + 1 :]:
            if name == "find" and word in _argv_rules._FIND_EXEC:
                break
            proven.add(origins[position])
    return proven - _executed_program_indices(tokens)


#: Programs that run a WORD OF THEIR OPERANDS as a program (``strace ssh``,
#: ``watch scp``), rather than only reading their operands as data.  After such a
#: runner only the one word it starts is a program position (resolved by
#: :func:`_runner_started_index`); that word, when it is a glob that can name
#: several ssh-family programs or an all-``*`` glob, is refused outright, while
#: the words after it are that started program's own operands and keep the
#: ordinary host checks.  Every OTHER program (``mypy``, ``npx``, ``shellcheck``,
#: an unknown name) only reads its operands as data, so such a glob there is not
#: refused outright; it falls through to the main walk's ordinary host checks.
#: ``find``'s ``-exec`` family is not listed: its command words are already not
#: proven operands (the scan in ``_proven_operand_indices`` breaks at
#: ``_FIND_EXEC``), so the glob there is read as run without needing a runner entry.
_KNOWN_RUNNERS = frozenset("strace ltrace valgrind watch gdb parallel".split())

#: For each known runner, the short options that take a VALUE in the next word
#: (``strace -e trace=openat``, ``watch -n 5``, ``parallel -j 4``).  The started
#: program is the first word after the runner's own options, so a value option
#: must consume its value or the value would be read as that program.  A
#: ``--opt=value`` word is one token and needs no entry; an option not listed is
#: assumed to take no value, which only reads the started program EARLIER (a miss
#: toward allow, never a new refusal).  ``valgrind`` uses ``--opt=value`` forms,
#: so it needs none.  ``gdb`` is special: only ``gdb --args <prog>`` starts a
#: program, handled in :func:`_runner_started_index`.
_RUNNER_VALUE_OPTS: "dict[str, frozenset[str]]" = {
    "strace": frozenset("-e -o -p -s -a -b -I -P -E -U -O -S -D -k".split()),
    "ltrace": frozenset("-e -o -p -s -a -l -x -X -u -S".split()),
    "watch": frozenset({"-n", "--interval"}),
    "parallel": frozenset("-j -P -n -N -L -s -I -X --jobs --max-procs".split()),
    "valgrind": frozenset(),
    "gdb": frozenset(),
}


def _runner_started_index(command: "list[tuple[int, str]]", idx: int, name: str) -> "int | None":
    """Index within *command* of the program a runner starts, or ``None``.

    *command* is a split command of ``(position, word)`` pairs and ``command[idx]``
    is the runner (``name``).  Bash runs only the word the runner starts, not every
    operand, so this skips the runner's own options -- consuming a value option's
    value (:data:`_RUNNER_VALUE_OPTS`) -- and returns the first word after them.
    ``gdb`` starts a program only through ``gdb --args <prog>``; every other
    ``gdb`` form loads a target without running an operand as a program.
    """
    if name == "gdb":
        for j in range(idx + 1, len(command)):
            if command[j][1] == "--args":
                return j + 1 if j + 1 < len(command) else None
        return None
    value_opts = _RUNNER_VALUE_OPTS.get(name, frozenset())
    j = idx + 1
    while j < len(command) and command[j][1].startswith("-") and command[j][1] != "-":
        if command[j][1] == "--":
            j += 1
            break
        j += 2 if command[j][1] in value_opts else 1
    return j if j < len(command) else None


def _case_arm_close_indices(words: "list[str]") -> "set[int]":
    """Indices of a standalone ``)`` word that TERMINATES a ``case`` arm pattern.

    A lone ``)`` word either closes a subshell (it balances an earlier lone
    ``(``) or ends a ``case`` arm pattern (``start)``, ``* )``).  Only the arm
    terminators are returned: the pattern word just before one is data, while a
    subshell's first word is a program bash runs.  Glued forms (``(s*)``,
    ``start)``) carry their own ``)`` inside one word and are read by the
    ``endswith(")")`` checks, so this tracks the spaced form alone.
    """
    out: set[int] = set()
    depth = 0
    for i, word in enumerate(words):
        if word == "(":
            depth += 1
        elif word == ")":
            if depth > 0:
                depth -= 1
            else:
                out.add(i)
    return out


def _runner_operand_indices(tokens: "list[str]") -> "set[int]":
    """Indices of the PROGRAM a :data:`_KNOWN_RUNNERS` runner starts.

    Such a program may RUN the word it starts (``strace``, ``ltrace``,
    ``valgrind``, ``watch``, ``gdb``, ``parallel``), so the ssh floor refuses a
    multi-name glob in that one started-program position outright and keeps an
    all-``*`` word there non-exempt.  Only that word counts: the runner's own
    options are skipped (:func:`_runner_started_index`), and the words after the
    started program are ITS operands, read with the ordinary host checks like any
    other program's operands -- so ``watch -n 5 ls -la logs/*`` and
    ``strace -f -e trace=openat cat config/*`` leave their path glob alone.
    ``parallel``'s words after ``:::`` are arguments, and ``gdb`` starts a program
    only through ``--args``, both handled by :func:`_runner_started_index`.  Every
    OTHER program only reads its operands as data, so its operands are left out and
    a glob there falls through to the ordinary host checks.  Left out too are the
    positions that are never run in this frame: array elements, a substitution's
    words, a comment's words, a ``case`` arm pattern (``s*)``, spaced ``* )``), and
    anything already in execute position.  A ``find`` ``-exec`` command's words are
    not proven either, so the floor already reads a glob there as run.
    """
    out: set[int] = set()
    words, origins = _cut_words(tokens)
    arm_closes = _case_arm_close_indices(words)
    for command in _argv_rules._split_commands_indexed(words):
        idx = _argv_rules._program_index([word for _, word in command])
        if idx is None:
            continue
        program = command[idx][1]
        spaced_arm = (
            idx + 1 < len(command)
            and command[idx + 1][1] == ")"
            and command[idx + 1][0] in arm_closes
        )
        if program == "case" or program.endswith(")") or spaced_arm:
            end = next((j for j in range(idx, len(command)) if command[j][1].endswith(")")), None)
            if end is None:
                continue
            body = _argv_rules._program_index([w for _, w in command[end + 1 :]])
            if body is None:
                continue
            idx = end + 1 + body
            program = command[idx][1]
        if program in ("for", "select") or "<" in program or ">" in program:
            continue
        name = _shell_normalizer._program_basename(program).removesuffix(".exe")
        if name not in _KNOWN_RUNNERS:
            continue
        started = _runner_started_index(command, idx, name)
        if started is not None:
            out.add(origins[command[started][0]])
    return (
        out
        - _executed_program_indices(tokens)
        - _shell_normalizer._array_element_indices(tokens)
        - _substitution_positions(tokens)
    )


def _glued_operator(token: str) -> "tuple[int, int]":
    """The span of the last control operator with a word after it in *token*, or (0, 0).

    ``x&&s?h`` gives (1, 3).  The ``&`` or ``|`` of a redirect operator is not a
    control operator: one right after ``<`` or ``>`` (``>&``, ``<&``, ``>|``,
    ``2>&1``) and an ``&`` right before ``>`` (``&>``, ``&>>``).  Bash reads
    ``>&cat s* x`` as one command whose program is ``s*``; cutting at that ``&``
    would make ``cat`` the program of a second command.
    """
    spans = []
    for match in _shell_normalizer._CONTROL_OPERATOR_RE.finditer(token):
        start, end = match.span()
        if start and token[start - 1] in "<>" and token[start] in "&|":
            start += 1
        if start < end and token[end - 1] == "&" and token[end : end + 1] == ">":
            end -= 1
        if start < end:
            spans.append((start, end))
    return next((span for span in reversed(spans) if span[1] < len(token)), (0, 0))


def _substitution_positions(tokens: "list[str]") -> "set[int]":
    """Indices of the words of the FIRST command inside a ``$( … )`` span.

    The word that opens a substitution is not inside it.  Only the span's first
    command is taken out: it is the one the resolver misreads (it steps over
    ``n=$(ls`` as an assignment).  After a separator the resolver finds each
    program itself, so those words stay in.

    Backticks are not read.  The tokens have lost their quotes, and a quoted
    backtick is not masked the way a quoted parenthesis is, so a count of them
    opens spans bash never opens and would take an executed verb out.  A word
    after a backtick opener therefore stays in execute position.
    """
    inside: set[int] = set()
    depth = 0
    first_command = True
    for i, token in enumerate(tokens):
        ends_command = _shell_normalizer._ends_argv(token)
        if depth > 0 and first_command and not ends_command:
            inside.add(i)
        depth = max(0, depth + token.count("$(") - token.count(")"))
        if depth == 0:
            first_command = True
        elif ends_command:
            first_command = False
    return inside


#: Readers whose stdin is data whatever their arguments are.
_PLAIN_HEREDOC_READERS = frozenset({"cat", "tee", "jq"})
#: ``git`` built-ins that read stdin as data, each with the words that make it do
#: so (empty: it reads stdin unasked).  A built-in cannot be aliased; any other
#: subcommand can be an alias that runs a shell.  The floor reads lowercased text.
_GIT_STDIN_READERS: "dict[str, tuple[tuple[str, ...], ...]]" = {
    "commit": (("-f", "-"), ("--file", "-"), ("--file=-",), ("-f-",)),
    "apply": (),
    "am": (),
    "mktree": (),
    "hash-object": (("--stdin",),),
    "update-index": (("--stdin",), ("--index-info",)),
}
#: ``gh`` core commands that take a body from stdin, with the words that ask for it.
_GH_STDIN_READERS: "dict[str, tuple[tuple[str, ...], ...]]" = {
    "pr": (("--body-file", "-"), ("--body-file=-",), ("-f", "-")),
    "issue": (("--body-file", "-"), ("--body-file=-",), ("-f", "-")),
    "release": (("--notes-file", "-"), ("--notes-file=-",), ("-f", "-")),
    "api": (("--input", "-"), ("--input=-",)),
}
#: Words that open or close a compound command, so a statement after one may be nested.
_COMPOUND_WORDS = frozenset(
    "{ } if then elif else fi for while until do done case esac select function time ! [[ coproc".split()
)
_READER_PROGRAM_RE = re.compile(r"(cat|tee|jq|git|gh)(?:\.exe)?")
_REDIRECT_RE = re.compile(r"\d*(<<-?|>>?)(.*)", re.DOTALL)
_PLAIN_PATH_RE = re.compile(r"[\w./~+:-]+")


def _has_words(words: "list[str]", wanted: "tuple[str, ...]") -> bool:
    """True when *wanted* appears in *words* as consecutive words."""
    span = len(wanted)
    return any(tuple(words[i : i + span]) == wanted for i in range(len(words) - span + 1))


def _reads_stdin_as_data(command: object) -> bool:
    """True when *command* is a known reader in a known form: its stdin is only data.

    The program is a bare ``cat``/``tee``/``jq``/``git``/``gh`` word (no path, no
    assignment prefix, no wrapper).  ``git`` and ``gh`` count only when the first
    argument is a built-in from the tables above and the words that make it read
    stdin are present, so a global option (``git -c alias.x=!sh``) or an alias or
    extension (``git x``, ``gh some-ext``) is not a reader.
    """
    words = [word.raw.replace("\\\n", "") for word in command.argv]  # type: ignore[attr-defined]
    words = [word for word in words if word]
    match = _READER_PROGRAM_RE.fullmatch(words[0]) if words else None
    if match is None:
        return False
    program = match.group(1)
    if program in _PLAIN_HEREDOC_READERS:
        return True
    forms = (_GIT_STDIN_READERS if program == "git" else _GH_STDIN_READERS).get(
        words[1] if len(words) > 1 else ""
    )
    if forms is None:
        return False
    return not forms or any(_has_words(words[2:], form) for form in forms)


def _statement_is_plain(command: object) -> bool:
    """True when *command* expands nothing and redirects only to plain file paths."""
    from .shell_commands import SUBST  # local: the facade serves every module name

    for step in command.steps:  # type: ignore[attr-defined]
        if step.text == SUBST or (step.text in ("$", "`") and step.state != 1):
            return False
    for word, target in command.redirects:  # type: ignore[attr-defined]
        match = _REDIRECT_RE.fullmatch(word.raw)
        if match is None or "<<<" in word.raw:
            return False
        if match.group(1).startswith(">"):
            path = match.group(2) or (target.raw if target is not None else "")
            if not _is_plain_path(path):
                return False
    return True


def _is_plain_path(path: str) -> bool:
    """True when *path* is a bare path word, or one quoted word that expands nothing."""
    if len(path) > 2 and path[0] == path[-1] and path[0] in "\"'":
        inner = path[1:-1]
        return not any(char in inner for char in (path[0], "$", "`", "*", "?", "[", "\n"))
    return _PLAIN_PATH_RE.fullmatch(path) is not None


def _text_without_data_heredocs(text_lower: str) -> str:
    """*text_lower* without the heredoc bodies that are certainly only data.

    ``shell_commands.read_commands`` is the one reader that pairs a ``<<`` with its
    closing tag; this floor pairs nothing itself.  A body is cut only when ALL of
    these hold, and stays in the text otherwise:

    * nothing in the text nests a statement (no parenthesis, backtick, substitution
      or compound-command word), so each pipeline (``shell_commands.pipelines``) is
      a top-level statement;
    * every segment is a known reader in a known form (:func:`_reads_stdin_as_data`),
      expands nothing and redirects only to plain file paths
      (:func:`_statement_is_plain`);
    * every heredoc of the pipeline has a closing tag line, and either a quoted
      tag (``<<'EOF'``, body expands nothing) or a body with no live substitution
      (no ``$(`` or backtick).  A literal ``*`` or ``ssh`` line of such a body is
      stdin data, while a body that runs a ``$(…)``/backtick stays in the text so
      the walk still reads it.
    """
    if "<<" not in text_lower:
        return text_lower
    from .shell_commands import SUBST, pipelines, read_commands  # local, as above

    try:
        commands = read_commands(text_lower, heredocs=True)
    except Exception:  # incl. RecursionError on deep nesting: cut nothing, walk the whole text
        return text_lower
    for command in commands:
        words = command.words
        if (
            command.separator in ("(", ")", "`")
            or any(step.text == SUBST for step in command.steps)
            or (words and words[0].raw in _COMPOUND_WORDS)
        ):
            return text_lower
    cuts: "list[tuple[int, int]]" = []
    for pipeline in pipelines(commands):
        docs = [doc for command in pipeline for doc in command.heredocs]
        if (
            docs
            and all(_reads_stdin_as_data(c) and _statement_is_plain(c) for c in pipeline)
            and all(
                (doc.quoted or ("$(" not in doc.body and "`" not in doc.body))
                and doc.end != doc.tail
                for doc in docs
            )
        ):
            cuts += [(doc.start, doc.end) for doc in docs]
    kept, prev = [], 0
    for start, end in sorted(cuts):
        kept.append(text_lower[prev:start])
        prev = end
    return "".join(kept) + text_lower[prev:]

from dataclasses import dataclass
from typing import Optional, Tuple, List
import json
import re
import actions

TELL_USER_PREFIX = "TELL_USER:"
READ_FILE_PREFIX = "READ_FILE:"
WRITE_FILE_PREFIX = "WRITE_FILE:"
APPEND_FILE_PREFIX = "APPEND_FILE:"
RUN_PYTHON_PREFIX = "RUN_PYTHON:"
SEARCH_ONLINE_PREFIX = "SEARCH_ONLINE:"
EXTRACT_INFO_PREFIX = "EXTRACT_INFO:"
SHUTDOWN_PREFIX = "SHUTDOWN"
FIND_AND_REPLACE_PREFIX = "FIND_AND_REPLACE:"
LIST_DIRECTORY_PREFIX = "LIST_DIRECTORY:"
CREATE_DIRECTORY_PREFIX = "CREATE_DIRECTORY:"
MEMORY_GET_PREFIX = "MEMORY_GET:"
MEMORY_SET_PREFIX = "MEMORY_SET:"


@dataclass(frozen=True)
class Metadata:
    reason: str
    plan: list[str]
    speak: Optional[str] = None

def parse_metadata(lines: List[str]) -> Metadata:
    if not lines:
        raise ValueError("Missing metadata in the response.")
    try:
        metadata_text = "\n".join(lines).strip()
        metadata_json = json.loads(metadata_text)
        return Metadata(
            reason=metadata_json["reason"],
            plan=metadata_json["plan"],
            speak=metadata_json.get("speak"),
        )
    except Exception as e:
        raise ValueError(f"Failed to parse metadata: {str(e)}\nMetadata text:\n{metadata_text}")

def find_metadata_lines(lines: List[str], content_start: int) -> Tuple[List[str], List[str]]:
    end_of_content = len(lines) - 1
    for i in range(len(lines) - 1, content_start, -1):
        if lines[i].startswith("```"):
            end_of_content = i
            break
    content_lines = lines[content_start:end_of_content]
    metadata_lines = lines[end_of_content + 1:]
    return content_lines, metadata_lines

def parse_memory_get_action(first_line: str, _: List[str]) -> Tuple[actions.MemoryGetAction, List[str]]:
    key = first_line[len(MEMORY_GET_PREFIX):].strip()
    return actions.MemoryGetAction(k=key), []

def parse_memory_set_action(first_line: str, _: List[str]) -> Tuple[actions.MemorySetAction, List[str]]:
    key, value = first_line[len(MEMORY_SET_PREFIX):].strip().split(",", 1)
    key = key.strip()
    value = value.strip()
    return actions.MemorySetAction(k=key, v=value), []


def parse_write_file_action(first_line: str, lines: List[str]) -> Tuple[actions.WriteFileAction, List[str]]:
    path = first_line[len(WRITE_FILE_PREFIX):].strip()
    content_lines, metadata_lines = find_metadata_lines(lines, 2)
    content_str = "\n".join(content_lines)
    action = actions.WriteFileAction(path=path, content=content_str)
    return action, metadata_lines

def parse_append_file_action(first_line: str, lines: List[str]) -> Tuple[actions.AppendFileAction, List[str]]:
    path = first_line[len(APPEND_FILE_PREFIX):].strip()
    content_lines, metadata_lines = find_metadata_lines(lines, 2)
    content_str = "\n".join(content_lines)
    action = actions.AppendFileAction(path=path, content=content_str)
    return action, metadata_lines

def parse_extract_info_action(first_line: str, _: List[str]) -> Tuple[actions.ExtractInfoAction, List[str]]:
    url, instruction = first_line[len(EXTRACT_INFO_PREFIX):].strip().split(",", 1)
    return actions.ExtractInfoAction(url, instruction), []

def parse_shutdown_action(first_line: str, _: List[str]) -> Tuple[actions.ShutdownAction, List[str]]:
    reason = first_line[len(SHUTDOWN_PREFIX):].strip()
    return actions.ShutdownAction(reason), []

def parse_create_directory_action(first_line: str, _: List[str]) -> Tuple[actions.CreateDirectoryAction, List[str]]:
    path = first_line[len(CREATE_DIRECTORY_PREFIX):].strip()
    return actions.CreateDirectoryAction(path), []

def parse_find_and_replace_action(first_line: str, lines: List[str]) -> Tuple[actions.FindAndReplaceAction, List[str]]:
    path = first_line.split(": ")[1].strip()

    find_start = lines.index("```")
    find_end = lines.index("```", find_start + 1)
    find_text = "\n".join(lines[find_start + 1:find_end])

    replace_start = lines.index("```", find_end + 1)
    replace_end = lines.index("```", replace_start + 1)
    replace_text = "\n".join(lines[replace_start + 1:replace_end])

    remaining_lines = lines[replace_end + 1:]

    return actions.FindAndReplaceAction(path, find_text, replace_text), remaining_lines


def parse_list_directory_action(first_line: str, _: List[str]) -> Tuple[actions.ListDirectoryAction, List[str]]:
    path = first_line[len(LIST_DIRECTORY_PREFIX):].strip()
    return actions.ListDirectoryAction(path), []


RUN_PYTHON_PREFIX = "RUN_PYTHON: "
RUN_PYTHON_PATTERN = re.compile(r"RUN_PYTHON: (.*), (\d+)")

def parse_run_python_action(line: str, lines: List[str]) -> Tuple[actions.RunPythonAction, List[str]]:
    match = RUN_PYTHON_PATTERN.match(line)
    if not match:
        raise ValueError(f"Invalid RUN_PYTHON action: {line}")
    path = match.group(1).strip()
    timeout = int(match.group(2))
    remaining_lines = lines[1:]  # Ignore the first line, as it has already been processed
    return actions.RunPythonAction(path=path, timeout=timeout), remaining_lines

action_parsers = [
    (TELL_USER_PREFIX, lambda line, _: (actions.TellUserAction(line[len(TELL_USER_PREFIX):].strip()), [])),
    (READ_FILE_PREFIX, lambda line, _: (actions.ReadFileAction(line[len(READ_FILE_PREFIX):].strip()), [])),
    (WRITE_FILE_PREFIX, parse_write_file_action),
    (APPEND_FILE_PREFIX, parse_append_file_action),
    (RUN_PYTHON_PREFIX, parse_run_python_action),
    (SEARCH_ONLINE_PREFIX, lambda line, _: (actions.SearchOnlineAction(line[len(SEARCH_ONLINE_PREFIX):].strip()), [])),
    (EXTRACT_INFO_PREFIX, parse_extract_info_action),
    (FIND_AND_REPLACE_PREFIX, parse_find_and_replace_action),
    (LIST_DIRECTORY_PREFIX, parse_list_directory_action),
    (SHUTDOWN_PREFIX, parse_shutdown_action),
    (CREATE_DIRECTORY_PREFIX, parse_create_directory_action),
    (MEMORY_GET_PREFIX, parse_memory_get_action),
    (MEMORY_SET_PREFIX, parse_memory_set_action),
]

def parse_action(first_line: str, lines: List[str]) -> Tuple[actions.Action, List[str]]:
    for prefix, parser in action_parsers:
        if first_line.startswith(prefix):
            return parser(first_line, lines)
    raise ValueError(f"Unknown action type in response: {first_line}")

def parse(text: str) -> Tuple[actions.Action, Metadata]:
    if not text:
        raise ValueError("Empty input received. Cannot parse.")
    print("Text:", text)

    lines = text.splitlines()
    action, metadata_lines = parse_action(lines[0], lines)

    if not metadata_lines:
        metadata = Metadata(reason="", plan=[])
    else:
        metadata = parse_metadata(metadata_lines)

    return action, metadata


"""Tests for the agent package.

These tests focus on the parts that can run *without* a real LLM:
- Tool correctness
- Config logic
- Graph structure (nodes, edges)
- State reducers

Tests that require a live API key are marked ``@pytest.mark.integration``
and skipped by default.  Run them with:

    uv run pytest -m integration
"""

from __future__ import annotations

import json
import os
import types
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.config import Settings, get_llm, get_settings
from agent.tools import *

# ---------------------------------------------------------------------------
# Tool tests — no LLM required
# ---------------------------------------------------------------------------


class TestCalculateTool:
    def test_basic_arithmetic(self) -> None:
        assert calculate.invoke("2 + 2") == "4"

    def test_operator_precedence(self) -> None:
        assert calculate.invoke("2 + 3 * 4") == "14"

    def test_parentheses(self) -> None:
        assert calculate.invoke("(2 + 3) * 4") == "20"

    def test_exponentiation(self) -> None:
        assert calculate.invoke("2 ** 10") == "1024"

    def test_float_result(self) -> None:
        result = calculate.invoke("7 / 2")
        assert result == "3.5"

    def test_floor_division(self) -> None:
        assert calculate.invoke("7 // 2") == "3"

    def test_modulo(self) -> None:
        assert calculate.invoke("10 % 3") == "1"

    def test_unary_negation(self) -> None:
        assert calculate.invoke("-5 + 10") == "5"

    def test_invalid_expression_returns_error(self) -> None:
        result = calculate.invoke("import os")
        assert result.startswith("Error:")

    def test_division_by_zero(self) -> None:
        result = calculate.invoke("1 / 0")
        assert result.startswith("Error:")


class TestGetCurrentDatetimeTool:
    def test_returns_iso_string(self) -> None:
        result = get_current_datetime.invoke({})
        # Should be parseable as an ISO-8601 datetime with timezone info.
        from datetime import datetime

        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None

    def test_returns_utc(self) -> None:
        result = get_current_datetime.invoke({})
        assert "+00:00" in result


class TestReadFileTool:
    def test_returns_file_content(self) -> None:
        result = read_file.invoke("tests/testfile.md")
        assert result == "Hello World 0815"

    def test_path_outside_project(self) -> None:
        result = read_file.invoke("../testfile.md")
        assert result.startswith("Error:")


class TestWriteReadFileTool:
    def test_roundtrip(self) -> None:
        test_string: str = "Hello world 1234"
        file_path: str = "tests/readwrite.md"
        write_file.invoke({"path": file_path, "content": test_string})
        result = read_file.invoke(file_path)
        assert result == test_string

    def test_roundtrip_with_offset(self) -> None:
        test_string: str = """All that glitters is not gold.
To be, or not to be, that is the question.
A rose by any other name would smell as sweet.
        """
        file_path: str = "tests/readwrite.md"
        write_file.invoke({"path": file_path, "content": test_string})
        result = read_file.invoke({"path": file_path, "offset": 1, "lines": 1})
        assert result == "To be, or not to be, that is the question.\n"


class TestReplaceInFileTool:
    def test_roundtrip(self) -> None:
        test_string: str = "Hello world 1234"
        file_path: str = "tests/readwrite.md"
        write_file.invoke({"path": file_path, "content": test_string})

        result_replace = replace_in_file.invoke(
            {"path": file_path, "old_string": "world", "new_string": "sun"}
        )
        assert result_replace == "Replaced 1 times"

        result = read_file.invoke(file_path)
        assert result == "Hello sun 1234"

    def test_roundtrip_multiple(self) -> None:
        test_string: str = """All that glitters is not gold.
To be, or not to be, that is the question.
A rose by any other name would smell as sweet.
        """
        file_path: str = "tests/readwrite.md"
        write_file.invoke({"path": file_path, "content": test_string})

        result_replace = replace_in_file.invoke(
            {"path": file_path, "old_string": "be", "new_string": "see"}
        )
        assert result_replace.startswith("Error:")

        result_replace = replace_in_file.invoke(
            {"path": file_path, "old_string": "123456", "new_string": "654321"}
        )
        assert result_replace.startswith("Error:")

        result = read_file.invoke({"path": file_path})
        assert result == test_string

        result_replace = replace_in_file.invoke(
            {
                "path": file_path,
                "old_string": "be",
                "new_string": "see",
                "replace_all": True,
            }
        )
        assert result_replace == "Replaced 2 times"

        result = read_file.invoke({"path": file_path})
        assert (
            result
            == """All that glitters is not gold.
To see, or not to see, that is the question.
A rose by any other name would smell as sweet.
        """
        )


class TestCreateDirectoryTool:
    def test_existing_parent_folder(self) -> None:
        new_folder = "tests/testfolder42"
        result = create_directory.invoke({"path": new_folder})
        assert result == "Success"
        assert os.path.isdir(new_folder)
        os.rmdir(new_folder)

    def test_recursive_creation(self) -> None:
        new_folder = "notexist/testfolder42"
        result = create_directory.invoke({"path": new_folder})
        assert result == "Success"
        assert os.path.isdir(new_folder)
        os.removedirs(new_folder)


class TestGrepTool:
    def test_grep(self) -> None:
        result = grep.invoke(
            {
                "pattern": "def",
                "directory": "tests/testfolder",
                "file_pattern": ["*.py"],
                "case_sensitive": False,
                "skip_dirs": {".venv"},
            }
        )

        assert result == "['tests/testfolder/folder1/test.py:2:def test():']"

    def test_grep_multi_file_extensions(self) -> None:
        result = grep.invoke(
            {
                "pattern": "def",
                "directory": "tests/testfolder",
                "file_pattern": ["*.py", "*.cpp"],
                "case_sensitive": False,
                "skip_dirs": {".venv"},
            }
        )

        assert (
            result
            == "['tests/testfolder/folder1/test.py:2:def test():', 'tests/testfolder/folder1/test.cpp:2:#define MAX 1000']"
        )

    def test_grep_too_many_lines(self) -> None:
        result = grep.invoke(
            {
                "pattern": "search_pattern",
                "directory": "tests/testfiles/",
                "file_pattern": ["long_file.txt"],
                "case_sensitive": False,
                "skip_dirs": {".venv"},
            }
        )
        assert (
            result
            == "{'truncated': True, 'total_matches': 1050, 'shown': 1000, 'results': ['tests/testfiles/long_file.txt:1:search_pattern', 'tests/testfiles/long_file.txt:2:search_pattern', 'tests/testfiles/long_file.txt:3:search_pattern', 'tests/testfiles/long_file.txt:4:search_pattern', 'tests/testfiles/long_file.txt:5:search_pattern', 'tests/testfiles/long_file.txt:6:search_pattern', 'tests/testfiles/long_file.txt:7:search_pattern', 'tests/testfiles/long_file.txt:8:search_pattern', 'tests/testfiles/long_file.txt:9:search_pattern', 'tests/testfiles/long_file.txt:10:search_pattern', 'tests/testfiles/long_file.txt:11:search_pattern', 'tests/testfiles/long_file.txt:12:search_pattern', 'tests/testfiles/long_file.txt:13:search_pattern', 'tests/testfiles/long_file.txt:14:search_pattern', 'tests/testfiles/long_file.txt:15:search_pattern', 'tests/testfiles/long_file.txt:16:search_pattern', 'tests/testfiles/long_file.txt:17:search_pattern', 'tests/testfiles/long_file.txt:18:search_pattern', 'tests/testfiles/long_file.txt:19:search_pattern', 'tests/testfiles/long_file.txt:20:search_pattern', 'tests/testfiles/long_file.txt:21:search_pattern', 'tests/testfiles/long_file.txt:22:search_pattern', 'tests/testfiles/long_file.txt:23:search_pattern', 'tests/testfiles/long_file.txt:24:search_pattern', 'tests/testfiles/long_file.txt:25:search_pattern', 'tests/testfiles/long_file.txt:26:search_pattern', 'tests/testfiles/long_file.txt:27:search_pattern', 'tests/testfiles/long_file.txt:28:search_pattern', 'tests/testfiles/long_file.txt:29:search_pattern', 'tests/testfiles/long_file.txt:30:search_pattern', 'tests/testfiles/long_file.txt:31:search_pattern', 'tests/testfiles/long_file.txt:32:search_pattern', 'tests/testfiles/long_file.txt:33:search_pattern', 'tests/testfiles/long_file.txt:34:search_pattern', 'tests/testfiles/long_file.txt:35:search_pattern', 'tests/testfiles/long_file.txt:36:search_pattern', 'tests/testfiles/long_file.txt:37:search_pattern', 'tests/testfiles/long_file.txt:38:search_pattern', 'tests/testfiles/long_file.txt:39:search_pattern', 'tests/testfiles/long_file.txt:40:search_pattern', 'tests/testfiles/long_file.txt:41:search_pattern', 'tests/testfiles/long_file.txt:42:search_pattern', 'tests/testfiles/long_file.txt:43:search_pattern', 'tests/testfiles/long_file.txt:44:search_pattern', 'tests/testfiles/long_file.txt:45:search_pattern', 'tests/testfiles/long_file.txt:46:search_pattern', 'tests/testfiles/long_file.txt:47:search_pattern', 'tests/testfiles/long_file.txt:48:search_pattern', 'tests/testfiles/long_file.txt:49:search_pattern', 'tests/testfiles/long_file.txt:50:search_pattern', 'tests/testfiles/long_file.txt:51:search_pattern', 'tests/testfiles/long_file.txt:52:search_pattern', 'tests/testfiles/long_file.txt:53:search_pattern', 'tests/testfiles/long_file.txt:54:search_pattern', 'tests/testfiles/long_file.txt:55:search_pattern', 'tests/testfiles/long_file.txt:56:search_pattern', 'tests/testfiles/long_file.txt:57:search_pattern', 'tests/testfiles/long_file.txt:58:search_pattern', 'tests/testfiles/long_file.txt:59:search_pattern', 'tests/testfiles/long_file.txt:60:search_pattern', 'tests/testfiles/long_file.txt:61:search_pattern', 'tests/testfiles/long_file.txt:62:search_pattern', 'tests/testfiles/long_file.txt:63:search_pattern', 'tests/testfiles/long_file.txt:64:search_pattern', 'tests/testfiles/long_file.txt:65:search_pattern', 'tests/testfiles/long_file.txt:66:search_pattern', 'tests/testfiles/long_file.txt:67:search_pattern', 'tests/testfiles/long_file.txt:68:search_pattern', 'tests/testfiles/long_file.txt:69:search_pattern', 'tests/testfiles/long_file.txt:70:search_pattern', 'tests/testfiles/long_file.txt:71:search_pattern', 'tests/testfiles/long_file.txt:72:search_pattern', 'tests/testfiles/long_file.txt:73:search_pattern', 'tests/testfiles/long_file.txt:74:search_pattern', 'tests/testfiles/long_file.txt:75:search_pattern', 'tests/testfiles/long_file.txt:76:search_pattern', 'tests/testfiles/long_file.txt:77:search_pattern', 'tests/testfiles/long_file.txt:78:search_pattern', 'tests/testfiles/long_file.txt:79:search_pattern', 'tests/testfiles/long_file.txt:80:search_pattern', 'tests/testfiles/long_file.txt:81:search_pattern', 'tests/testfiles/long_file.txt:82:search_pattern', 'tests/testfiles/long_file.txt:83:search_pattern', 'tests/testfiles/long_file.txt:84:search_pattern', 'tests/testfiles/long_file.txt:85:search_pattern', 'tests/testfiles/long_file.txt:86:search_pattern', 'tests/testfiles/long_file.txt:87:search_pattern', 'tests/testfiles/long_file.txt:88:search_pattern', 'tests/testfiles/long_file.txt:89:search_pattern', 'tests/testfiles/long_file.txt:90:search_pattern', 'tests/testfiles/long_file.txt:91:search_pattern', 'tests/testfiles/long_file.txt:92:search_pattern', 'tests/testfiles/long_file.txt:93:search_pattern', 'tests/testfiles/long_file.txt:94:search_pattern', 'tests/testfiles/long_file.txt:95:search_pattern', 'tests/testfiles/long_file.txt:96:search_pattern', 'tests/testfiles/long_file.txt:97:search_pattern', 'tests/testfiles/long_file.txt:98:search_pattern', 'tests/testfiles/long_file.txt:99:search_pattern', 'tests/testfiles/long_file.txt:100:search_pattern', 'tests/testfiles/long_file.txt:101:search_pattern', 'tests/testfiles/long_file.txt:102:search_pattern', 'tests/testfiles/long_file.txt:103:search_pattern', 'tests/testfiles/long_file.txt:104:search_pattern', 'tests/testfiles/long_file.txt:105:search_pattern', 'tests/testfiles/long_file.txt:106:search_pattern', 'tests/testfiles/long_file.txt:107:search_pattern', 'tests/testfiles/long_file.txt:108:search_pattern', 'tests/testfiles/long_file.txt:109:search_pattern', 'tests/testfiles/long_file.txt:110:search_pattern', 'tests/testfiles/long_file.txt:111:search_pattern', 'tests/testfiles/long_file.txt:112:search_pattern', 'tests/testfiles/long_file.txt:113:search_pattern', 'tests/testfiles/long_file.txt:114:search_pattern', 'tests/testfiles/long_file.txt:115:search_pattern', 'tests/testfiles/long_file.txt:116:search_pattern', 'tests/testfiles/long_file.txt:117:search_pattern', 'tests/testfiles/long_file.txt:118:search_pattern', 'tests/testfiles/long_file.txt:119:search_pattern', 'tests/testfiles/long_file.txt:120:search_pattern', 'tests/testfiles/long_file.txt:121:search_pattern', 'tests/testfiles/long_file.txt:122:search_pattern', 'tests/testfiles/long_file.txt:123:search_pattern', 'tests/testfiles/long_file.txt:124:search_pattern', 'tests/testfiles/long_file.txt:125:search_pattern', 'tests/testfiles/long_file.txt:126:search_pattern', 'tests/testfiles/long_file.txt:127:search_pattern', 'tests/testfiles/long_file.txt:128:search_pattern', 'tests/testfiles/long_file.txt:129:search_pattern', 'tests/testfiles/long_file.txt:130:search_pattern', 'tests/testfiles/long_file.txt:131:search_pattern', 'tests/testfiles/long_file.txt:132:search_pattern', 'tests/testfiles/long_file.txt:133:search_pattern', 'tests/testfiles/long_file.txt:134:search_pattern', 'tests/testfiles/long_file.txt:135:search_pattern', 'tests/testfiles/long_file.txt:136:search_pattern', 'tests/testfiles/long_file.txt:137:search_pattern', 'tests/testfiles/long_file.txt:138:search_pattern', 'tests/testfiles/long_file.txt:139:search_pattern', 'tests/testfiles/long_file.txt:140:search_pattern', 'tests/testfiles/long_file.txt:141:search_pattern', 'tests/testfiles/long_file.txt:142:search_pattern', 'tests/testfiles/long_file.txt:143:search_pattern', 'tests/testfiles/long_file.txt:144:search_pattern', 'tests/testfiles/long_file.txt:145:search_pattern', 'tests/testfiles/long_file.txt:146:search_pattern', 'tests/testfiles/long_file.txt:147:search_pattern', 'tests/testfiles/long_file.txt:148:search_pattern', 'tests/testfiles/long_file.txt:149:search_pattern', 'tests/testfiles/long_file.txt:150:search_pattern', 'tests/testfiles/long_file.txt:151:search_pattern', 'tests/testfiles/long_file.txt:152:search_pattern', 'tests/testfiles/long_file.txt:153:search_pattern', 'tests/testfiles/long_file.txt:154:search_pattern', 'tests/testfiles/long_file.txt:155:search_pattern', 'tests/testfiles/long_file.txt:156:search_pattern', 'tests/testfiles/long_file.txt:157:search_pattern', 'tests/testfiles/long_file.txt:158:search_pattern', 'tests/testfiles/long_file.txt:159:search_pattern', 'tests/testfiles/long_file.txt:160:search_pattern', 'tests/testfiles/long_file.txt:161:search_pattern', 'tests/testfiles/long_file.txt:162:search_pattern', 'tests/testfiles/long_file.txt:163:search_pattern', 'tests/testfiles/long_file.txt:164:search_pattern', 'tests/testfiles/long_file.txt:165:search_pattern', 'tests/testfiles/long_file.txt:166:search_pattern', 'tests/testfiles/long_file.txt:167:search_pattern', 'tests/testfiles/long_file.txt:168:search_pattern', 'tests/testfiles/long_file.txt:169:search_pattern', 'tests/testfiles/long_file.txt:170:search_pattern', 'tests/testfiles/long_file.txt:171:search_pattern', 'tests/testfiles/long_file.txt:172:search_pattern', 'tests/testfiles/long_file.txt:173:search_pattern', 'tests/testfiles/long_file.txt:174:search_pattern', 'tests/testfiles/long_file.txt:175:search_pattern', 'tests/testfiles/long_file.txt:176:search_pattern', 'tests/testfiles/long_file.txt:177:search_pattern', 'tests/testfiles/long_file.txt:178:search_pattern', 'tests/testfiles/long_file.txt:179:search_pattern', 'tests/testfiles/long_file.txt:180:search_pattern', 'tests/testfiles/long_file.txt:181:search_pattern', 'tests/testfiles/long_file.txt:182:search_pattern', 'tests/testfiles/long_file.txt:183:search_pattern', 'tests/testfiles/long_file.txt:184:search_pattern', 'tests/testfiles/long_file.txt:185:search_pattern', 'tests/testfiles/long_file.txt:186:search_pattern', 'tests/testfiles/long_file.txt:187:search_pattern', 'tests/testfiles/long_file.txt:188:search_pattern', 'tests/testfiles/long_file.txt:189:search_pattern', 'tests/testfiles/long_file.txt:190:search_pattern', 'tests/testfiles/long_file.txt:191:search_pattern', 'tests/testfiles/long_file.txt:192:search_pattern', 'tests/testfiles/long_file.txt:193:search_pattern', 'tests/testfiles/long_file.txt:194:search_pattern', 'tests/testfiles/long_file.txt:195:search_pattern', 'tests/testfiles/long_file.txt:196:search_pattern', 'tests/testfiles/long_file.txt:197:search_pattern', 'tests/testfiles/long_file.txt:198:search_pattern', 'tests/testfiles/long_file.txt:199:search_pattern', 'tests/testfiles/long_file.txt:200:search_pattern', 'tests/testfiles/long_file.txt:201:search_pattern', 'tests/testfiles/long_file.txt:202:search_pattern', 'tests/testfiles/long_file.txt:203:search_pattern', 'tests/testfiles/long_file.txt:204:search_pattern', 'tests/testfiles/long_file.txt:205:search_pattern', 'tests/testfiles/long_file.txt:206:search_pattern', 'tests/testfiles/long_file.txt:207:search_pattern', 'tests/testfiles/long_file.txt:208:search_pattern', 'tests/testfiles/long_file.txt:209:search_pattern', 'tests/testfiles/long_file.txt:210:search_pattern', 'tests/testfiles/long_file.txt:211:search_pattern', 'tests/testfiles/long_file.txt:212:search_pattern', 'tests/testfiles/long_file.txt:213:search_pattern', 'tests/testfiles/long_file.txt:214:search_pattern', 'tests/testfiles/long_file.txt:215:search_pattern', 'tests/testfiles/long_file.txt:216:search_pattern', 'tests/testfiles/long_file.txt:217:search_pattern', 'tests/testfiles/long_file.txt:218:search_pattern', 'tests/testfiles/long_file.txt:219:search_pattern', 'tests/testfiles/long_file.txt:220:search_pattern', 'tests/testfiles/long_file.txt:221:search_pattern', 'tests/testfiles/long_file.txt:222:search_pattern', 'tests/testfiles/long_file.txt:223:search_pattern', 'tests/testfiles/long_file.txt:224:search_pattern', 'tests/testfiles/long_file.txt:225:search_pattern', 'tests/testfiles/long_file.txt:226:search_pattern', 'tests/testfiles/long_file.txt:227:search_pattern', 'tests/testfiles/long_file.txt:228:search_pattern', 'tests/testfiles/long_file.txt:229:search_pattern', 'tests/testfiles/long_file.txt:230:search_pattern', 'tests/testfiles/long_file.txt:231:search_pattern', 'tests/testfiles/long_file.txt:232:search_pattern', 'tests/testfiles/long_file.txt:233:search_pattern', 'tests/testfiles/long_file.txt:234:search_pattern', 'tests/testfiles/long_file.txt:235:search_pattern', 'tests/testfiles/long_file.txt:236:search_pattern', 'tests/testfiles/long_file.txt:237:search_pattern', 'tests/testfiles/long_file.txt:238:search_pattern', 'tests/testfiles/long_file.txt:239:search_pattern', 'tests/testfiles/long_file.txt:240:search_pattern', 'tests/testfiles/long_file.txt:241:search_pattern', 'tests/testfiles/long_file.txt:242:search_pattern', 'tests/testfiles/long_file.txt:243:search_pattern', 'tests/testfiles/long_file.txt:244:search_pattern', 'tests/testfiles/long_file.txt:245:search_pattern', 'tests/testfiles/long_file.txt:246:search_pattern', 'tests/testfiles/long_file.txt:247:search_pattern', 'tests/testfiles/long_file.txt:248:search_pattern', 'tests/testfiles/long_file.txt:249:search_pattern', 'tests/testfiles/long_file.txt:250:search_pattern', 'tests/testfiles/long_file.txt:251:search_pattern', 'tests/testfiles/long_file.txt:252:search_pattern', 'tests/testfiles/long_file.txt:253:search_pattern', 'tests/testfiles/long_file.txt:254:search_pattern', 'tests/testfiles/long_file.txt:255:search_pattern', 'tests/testfiles/long_file.txt:256:search_pattern', 'tests/testfiles/long_file.txt:257:search_pattern', 'tests/testfiles/long_file.txt:258:search_pattern', 'tests/testfiles/long_file.txt:259:search_pattern', 'tests/testfiles/long_file.txt:260:search_pattern', 'tests/testfiles/long_file.txt:261:search_pattern', 'tests/testfiles/long_file.txt:262:search_pattern', 'tests/testfiles/long_file.txt:263:search_pattern', 'tests/testfiles/long_file.txt:264:search_pattern', 'tests/testfiles/long_file.txt:265:search_pattern', 'tests/testfiles/long_file.txt:266:search_pattern', 'tests/testfiles/long_file.txt:267:search_pattern', 'tests/testfiles/long_file.txt:268:search_pattern', 'tests/testfiles/long_file.txt:269:search_pattern', 'tests/testfiles/long_file.txt:270:search_pattern', 'tests/testfiles/long_file.txt:271:search_pattern', 'tests/testfiles/long_file.txt:272:search_pattern', 'tests/testfiles/long_file.txt:273:search_pattern', 'tests/testfiles/long_file.txt:274:search_pattern', 'tests/testfiles/long_file.txt:275:search_pattern', 'tests/testfiles/long_file.txt:276:search_pattern', 'tests/testfiles/long_file.txt:277:search_pattern', 'tests/testfiles/long_file.txt:278:search_pattern', 'tests/testfiles/long_file.txt:279:search_pattern', 'tests/testfiles/long_file.txt:280:search_pattern', 'tests/testfiles/long_file.txt:281:search_pattern', 'tests/testfiles/long_file.txt:282:search_pattern', 'tests/testfiles/long_file.txt:283:search_pattern', 'tests/testfiles/long_file.txt:284:search_pattern', 'tests/testfiles/long_file.txt:285:search_pattern', 'tests/testfiles/long_file.txt:286:search_pattern', 'tests/testfiles/long_file.txt:287:search_pattern', 'tests/testfiles/long_file.txt:288:search_pattern', 'tests/testfiles/long_file.txt:289:search_pattern', 'tests/testfiles/long_file.txt:290:search_pattern', 'tests/testfiles/long_file.txt:291:search_pattern', 'tests/testfiles/long_file.txt:292:search_pattern', 'tests/testfiles/long_file.txt:293:search_pattern', 'tests/testfiles/long_file.txt:294:search_pattern', 'tests/testfiles/long_file.txt:295:search_pattern', 'tests/testfiles/long_file.txt:296:search_pattern', 'tests/testfiles/long_file.txt:297:search_pattern', 'tests/testfiles/long_file.txt:298:search_pattern', 'tests/testfiles/long_file.txt:299:search_pattern', 'tests/testfiles/long_file.txt:300:search_pattern', 'tests/testfiles/long_file.txt:301:search_pattern', 'tests/testfiles/long_file.txt:302:search_pattern', 'tests/testfiles/long_file.txt:303:search_pattern', 'tests/testfiles/long_file.txt:304:search_pattern', 'tests/testfiles/long_file.txt:305:search_pattern', 'tests/testfiles/long_file.txt:306:search_pattern', 'tests/testfiles/long_file.txt:307:search_pattern', 'tests/testfiles/long_file.txt:308:search_pattern', 'tests/testfiles/long_file.txt:309:search_pattern', 'tests/testfiles/long_file.txt:310:search_pattern', 'tests/testfiles/long_file.txt:311:search_pattern', 'tests/testfiles/long_file.txt:312:search_pattern', 'tests/testfiles/long_file.txt:313:search_pattern', 'tests/testfiles/long_file.txt:314:search_pattern', 'tests/testfiles/long_file.txt:315:search_pattern', 'tests/testfiles/long_file.txt:316:search_pattern', 'tests/testfiles/long_file.txt:317:search_pattern', 'tests/testfiles/long_file.txt:318:search_pattern', 'tests/testfiles/long_file.txt:319:search_pattern', 'tests/testfiles/long_file.txt:320:search_pattern', 'tests/testfiles/long_file.txt:321:search_pattern', 'tests/testfiles/long_file.txt:322:search_pattern', 'tests/testfiles/long_file.txt:323:search_pattern', 'tests/testfiles/long_file.txt:324:search_pattern', 'tests/testfiles/long_file.txt:325:search_pattern', 'tests/testfiles/long_file.txt:326:search_pattern', 'tests/testfiles/long_file.txt:327:search_pattern', 'tests/testfiles/long_file.txt:328:search_pattern', 'tests/testfiles/long_file.txt:329:search_pattern', 'tests/testfiles/long_file.txt:330:search_pattern', 'tests/testfiles/long_file.txt:331:search_pattern', 'tests/testfiles/long_file.txt:332:search_pattern', 'tests/testfiles/long_file.txt:333:search_pattern', 'tests/testfiles/long_file.txt:334:search_pattern', 'tests/testfiles/long_file.txt:335:search_pattern', 'tests/testfiles/long_file.txt:336:search_pattern', 'tests/testfiles/long_file.txt:337:search_pattern', 'tests/testfiles/long_file.txt:338:search_pattern', 'tests/testfiles/long_file.txt:339:search_pattern', 'tests/testfiles/long_file.txt:340:search_pattern', 'tests/testfiles/long_file.txt:341:search_pattern', 'tests/testfiles/long_file.txt:342:search_pattern', 'tests/testfiles/long_file.txt:343:search_pattern', 'tests/testfiles/long_file.txt:344:search_pattern', 'tests/testfiles/long_file.txt:345:search_pattern', 'tests/testfiles/long_file.txt:346:search_pattern', 'tests/testfiles/long_file.txt:347:search_pattern', 'tests/testfiles/long_file.txt:348:search_pattern', 'tests/testfiles/long_file.txt:349:search_pattern', 'tests/testfiles/long_file.txt:350:search_pattern', 'tests/testfiles/long_file.txt:351:search_pattern', 'tests/testfiles/long_file.txt:352:search_pattern', 'tests/testfiles/long_file.txt:353:search_pattern', 'tests/testfiles/long_file.txt:354:search_pattern', 'tests/testfiles/long_file.txt:355:search_pattern', 'tests/testfiles/long_file.txt:356:search_pattern', 'tests/testfiles/long_file.txt:357:search_pattern', 'tests/testfiles/long_file.txt:358:search_pattern', 'tests/testfiles/long_file.txt:359:search_pattern', 'tests/testfiles/long_file.txt:360:search_pattern', 'tests/testfiles/long_file.txt:361:search_pattern', 'tests/testfiles/long_file.txt:362:search_pattern', 'tests/testfiles/long_file.txt:363:search_pattern', 'tests/testfiles/long_file.txt:364:search_pattern', 'tests/testfiles/long_file.txt:365:search_pattern', 'tests/testfiles/long_file.txt:366:search_pattern', 'tests/testfiles/long_file.txt:367:search_pattern', 'tests/testfiles/long_file.txt:368:search_pattern', 'tests/testfiles/long_file.txt:369:search_pattern', 'tests/testfiles/long_file.txt:370:search_pattern', 'tests/testfiles/long_file.txt:371:search_pattern', 'tests/testfiles/long_file.txt:372:search_pattern', 'tests/testfiles/long_file.txt:373:search_pattern', 'tests/testfiles/long_file.txt:374:search_pattern', 'tests/testfiles/long_file.txt:375:search_pattern', 'tests/testfiles/long_file.txt:376:search_pattern', 'tests/testfiles/long_file.txt:377:search_pattern', 'tests/testfiles/long_file.txt:378:search_pattern', 'tests/testfiles/long_file.txt:379:search_pattern', 'tests/testfiles/long_file.txt:380:search_pattern', 'tests/testfiles/long_file.txt:381:search_pattern', 'tests/testfiles/long_file.txt:382:search_pattern', 'tests/testfiles/long_file.txt:383:search_pattern', 'tests/testfiles/long_file.txt:384:search_pattern', 'tests/testfiles/long_file.txt:385:search_pattern', 'tests/testfiles/long_file.txt:386:search_pattern', 'tests/testfiles/long_file.txt:387:search_pattern', 'tests/testfiles/long_file.txt:388:search_pattern', 'tests/testfiles/long_file.txt:389:search_pattern', 'tests/testfiles/long_file.txt:390:search_pattern', 'tests/testfiles/long_file.txt:391:search_pattern', 'tests/testfiles/long_file.txt:392:search_pattern', 'tests/testfiles/long_file.txt:393:search_pattern', 'tests/testfiles/long_file.txt:394:search_pattern', 'tests/testfiles/long_file.txt:395:search_pattern', 'tests/testfiles/long_file.txt:396:search_pattern', 'tests/testfiles/long_file.txt:397:search_pattern', 'tests/testfiles/long_file.txt:398:search_pattern', 'tests/testfiles/long_file.txt:399:search_pattern', 'tests/testfiles/long_file.txt:400:search_pattern', 'tests/testfiles/long_file.txt:401:search_pattern', 'tests/testfiles/long_file.txt:402:search_pattern', 'tests/testfiles/long_file.txt:403:search_pattern', 'tests/testfiles/long_file.txt:404:search_pattern', 'tests/testfiles/long_file.txt:405:search_pattern', 'tests/testfiles/long_file.txt:406:search_pattern', 'tests/testfiles/long_file.txt:407:search_pattern', 'tests/testfiles/long_file.txt:408:search_pattern', 'tests/testfiles/long_file.txt:409:search_pattern', 'tests/testfiles/long_file.txt:410:search_pattern', 'tests/testfiles/long_file.txt:411:search_pattern', 'tests/testfiles/long_file.txt:412:search_pattern', 'tests/testfiles/long_file.txt:413:search_pattern', 'tests/testfiles/long_file.txt:414:search_pattern', 'tests/testfiles/long_file.txt:415:search_pattern', 'tests/testfiles/long_file.txt:416:search_pattern', 'tests/testfiles/long_file.txt:417:search_pattern', 'tests/testfiles/long_file.txt:418:search_pattern', 'tests/testfiles/long_file.txt:419:search_pattern', 'tests/testfiles/long_file.txt:420:search_pattern', 'tests/testfiles/long_file.txt:421:search_pattern', 'tests/testfiles/long_file.txt:422:search_pattern', 'tests/testfiles/long_file.txt:423:search_pattern', 'tests/testfiles/long_file.txt:424:search_pattern', 'tests/testfiles/long_file.txt:425:search_pattern', 'tests/testfiles/long_file.txt:426:search_pattern', 'tests/testfiles/long_file.txt:427:search_pattern', 'tests/testfiles/long_file.txt:428:search_pattern', 'tests/testfiles/long_file.txt:429:search_pattern', 'tests/testfiles/long_file.txt:430:search_pattern', 'tests/testfiles/long_file.txt:431:search_pattern', 'tests/testfiles/long_file.txt:432:search_pattern', 'tests/testfiles/long_file.txt:433:search_pattern', 'tests/testfiles/long_file.txt:434:search_pattern', 'tests/testfiles/long_file.txt:435:search_pattern', 'tests/testfiles/long_file.txt:436:search_pattern', 'tests/testfiles/long_file.txt:437:search_pattern', 'tests/testfiles/long_file.txt:438:search_pattern', 'tests/testfiles/long_file.txt:439:search_pattern', 'tests/testfiles/long_file.txt:440:search_pattern', 'tests/testfiles/long_file.txt:441:search_pattern', 'tests/testfiles/long_file.txt:442:search_pattern', 'tests/testfiles/long_file.txt:443:search_pattern', 'tests/testfiles/long_file.txt:444:search_pattern', 'tests/testfiles/long_file.txt:445:search_pattern', 'tests/testfiles/long_file.txt:446:search_pattern', 'tests/testfiles/long_file.txt:447:search_pattern', 'tests/testfiles/long_file.txt:448:search_pattern', 'tests/testfiles/long_file.txt:449:search_pattern', 'tests/testfiles/long_file.txt:450:search_pattern', 'tests/testfiles/long_file.txt:451:search_pattern', 'tests/testfiles/long_file.txt:452:search_pattern', 'tests/testfiles/long_file.txt:453:search_pattern', 'tests/testfiles/long_file.txt:454:search_pattern', 'tests/testfiles/long_file.txt:455:search_pattern', 'tests/testfiles/long_file.txt:456:search_pattern', 'tests/testfiles/long_file.txt:457:search_pattern', 'tests/testfiles/long_file.txt:458:search_pattern', 'tests/testfiles/long_file.txt:459:search_pattern', 'tests/testfiles/long_file.txt:460:search_pattern', 'tests/testfiles/long_file.txt:461:search_pattern', 'tests/testfiles/long_file.txt:462:search_pattern', 'tests/testfiles/long_file.txt:463:search_pattern', 'tests/testfiles/long_file.txt:464:search_pattern', 'tests/testfiles/long_file.txt:465:search_pattern', 'tests/testfiles/long_file.txt:466:search_pattern', 'tests/testfiles/long_file.txt:467:search_pattern', 'tests/testfiles/long_file.txt:468:search_pattern', 'tests/testfiles/long_file.txt:469:search_pattern', 'tests/testfiles/long_file.txt:470:search_pattern', 'tests/testfiles/long_file.txt:471:search_pattern', 'tests/testfiles/long_file.txt:472:search_pattern', 'tests/testfiles/long_file.txt:473:search_pattern', 'tests/testfiles/long_file.txt:474:search_pattern', 'tests/testfiles/long_file.txt:475:search_pattern', 'tests/testfiles/long_file.txt:476:search_pattern', 'tests/testfiles/long_file.txt:477:search_pattern', 'tests/testfiles/long_file.txt:478:search_pattern', 'tests/testfiles/long_file.txt:479:search_pattern', 'tests/testfiles/long_file.txt:480:search_pattern', 'tests/testfiles/long_file.txt:481:search_pattern', 'tests/testfiles/long_file.txt:482:search_pattern', 'tests/testfiles/long_file.txt:483:search_pattern', 'tests/testfiles/long_file.txt:484:search_pattern', 'tests/testfiles/long_file.txt:485:search_pattern', 'tests/testfiles/long_file.txt:486:search_pattern', 'tests/testfiles/long_file.txt:487:search_pattern', 'tests/testfiles/long_file.txt:488:search_pattern', 'tests/testfiles/long_file.txt:489:search_pattern', 'tests/testfiles/long_file.txt:490:search_pattern', 'tests/testfiles/long_file.txt:491:search_pattern', 'tests/testfiles/long_file.txt:492:search_pattern', 'tests/testfiles/long_file.txt:493:search_pattern', 'tests/testfiles/long_file.txt:494:search_pattern', 'tests/testfiles/long_file.txt:495:search_pattern', 'tests/testfiles/long_file.txt:496:search_pattern', 'tests/testfiles/long_file.txt:497:search_pattern', 'tests/testfiles/long_file.txt:498:search_pattern', 'tests/testfiles/long_file.txt:499:search_pattern', 'tests/testfiles/long_file.txt:500:search_pattern', 'tests/testfiles/long_file.txt:501:search_pattern', 'tests/testfiles/long_file.txt:502:search_pattern', 'tests/testfiles/long_file.txt:503:search_pattern', 'tests/testfiles/long_file.txt:504:search_pattern', 'tests/testfiles/long_file.txt:505:search_pattern', 'tests/testfiles/long_file.txt:506:search_pattern', 'tests/testfiles/long_file.txt:507:search_pattern', 'tests/testfiles/long_file.txt:508:search_pattern', 'tests/testfiles/long_file.txt:509:search_pattern', 'tests/testfiles/long_file.txt:510:search_pattern', 'tests/testfiles/long_file.txt:511:search_pattern', 'tests/testfiles/long_file.txt:512:search_pattern', 'tests/testfiles/long_file.txt:513:search_pattern', 'tests/testfiles/long_file.txt:514:search_pattern', 'tests/testfiles/long_file.txt:515:search_pattern', 'tests/testfiles/long_file.txt:516:search_pattern', 'tests/testfiles/long_file.txt:517:search_pattern', 'tests/testfiles/long_file.txt:518:search_pattern', 'tests/testfiles/long_file.txt:519:search_pattern', 'tests/testfiles/long_file.txt:520:search_pattern', 'tests/testfiles/long_file.txt:521:search_pattern', 'tests/testfiles/long_file.txt:522:search_pattern', 'tests/testfiles/long_file.txt:523:search_pattern', 'tests/testfiles/long_file.txt:524:search_pattern', 'tests/testfiles/long_file.txt:525:search_pattern', 'tests/testfiles/long_file.txt:526:search_pattern', 'tests/testfiles/long_file.txt:527:search_pattern', 'tests/testfiles/long_file.txt:528:search_pattern', 'tests/testfiles/long_file.txt:529:search_pattern', 'tests/testfiles/long_file.txt:530:search_pattern', 'tests/testfiles/long_file.txt:531:search_pattern', 'tests/testfiles/long_file.txt:532:search_pattern', 'tests/testfiles/long_file.txt:533:search_pattern', 'tests/testfiles/long_file.txt:534:search_pattern', 'tests/testfiles/long_file.txt:535:search_pattern', 'tests/testfiles/long_file.txt:536:search_pattern', 'tests/testfiles/long_file.txt:537:search_pattern', 'tests/testfiles/long_file.txt:538:search_pattern', 'tests/testfiles/long_file.txt:539:search_pattern', 'tests/testfiles/long_file.txt:540:search_pattern', 'tests/testfiles/long_file.txt:541:search_pattern', 'tests/testfiles/long_file.txt:542:search_pattern', 'tests/testfiles/long_file.txt:543:search_pattern', 'tests/testfiles/long_file.txt:544:search_pattern', 'tests/testfiles/long_file.txt:545:search_pattern', 'tests/testfiles/long_file.txt:546:search_pattern', 'tests/testfiles/long_file.txt:547:search_pattern', 'tests/testfiles/long_file.txt:548:search_pattern', 'tests/testfiles/long_file.txt:549:search_pattern', 'tests/testfiles/long_file.txt:550:search_pattern', 'tests/testfiles/long_file.txt:551:search_pattern', 'tests/testfiles/long_file.txt:552:search_pattern', 'tests/testfiles/long_file.txt:553:search_pattern', 'tests/testfiles/long_file.txt:554:search_pattern', 'tests/testfiles/long_file.txt:555:search_pattern', 'tests/testfiles/long_file.txt:556:search_pattern', 'tests/testfiles/long_file.txt:557:search_pattern', 'tests/testfiles/long_file.txt:558:search_pattern', 'tests/testfiles/long_file.txt:559:search_pattern', 'tests/testfiles/long_file.txt:560:search_pattern', 'tests/testfiles/long_file.txt:561:search_pattern', 'tests/testfiles/long_file.txt:562:search_pattern', 'tests/testfiles/long_file.txt:563:search_pattern', 'tests/testfiles/long_file.txt:564:search_pattern', 'tests/testfiles/long_file.txt:565:search_pattern', 'tests/testfiles/long_file.txt:566:search_pattern', 'tests/testfiles/long_file.txt:567:search_pattern', 'tests/testfiles/long_file.txt:568:search_pattern', 'tests/testfiles/long_file.txt:569:search_pattern', 'tests/testfiles/long_file.txt:570:search_pattern', 'tests/testfiles/long_file.txt:571:search_pattern', 'tests/testfiles/long_file.txt:572:search_pattern', 'tests/testfiles/long_file.txt:573:search_pattern', 'tests/testfiles/long_file.txt:574:search_pattern', 'tests/testfiles/long_file.txt:575:search_pattern', 'tests/testfiles/long_file.txt:576:search_pattern', 'tests/testfiles/long_file.txt:577:search_pattern', 'tests/testfiles/long_file.txt:578:search_pattern', 'tests/testfiles/long_file.txt:579:search_pattern', 'tests/testfiles/long_file.txt:580:search_pattern', 'tests/testfiles/long_file.txt:581:search_pattern', 'tests/testfiles/long_file.txt:582:search_pattern', 'tests/testfiles/long_file.txt:583:search_pattern', 'tests/testfiles/long_file.txt:584:search_pattern', 'tests/testfiles/long_file.txt:585:search_pattern', 'tests/testfiles/long_file.txt:586:search_pattern', 'tests/testfiles/long_file.txt:587:search_pattern', 'tests/testfiles/long_file.txt:588:search_pattern', 'tests/testfiles/long_file.txt:589:search_pattern', 'tests/testfiles/long_file.txt:590:search_pattern', 'tests/testfiles/long_file.txt:591:search_pattern', 'tests/testfiles/long_file.txt:592:search_pattern', 'tests/testfiles/long_file.txt:593:search_pattern', 'tests/testfiles/long_file.txt:594:search_pattern', 'tests/testfiles/long_file.txt:595:search_pattern', 'tests/testfiles/long_file.txt:596:search_pattern', 'tests/testfiles/long_file.txt:597:search_pattern', 'tests/testfiles/long_file.txt:598:search_pattern', 'tests/testfiles/long_file.txt:599:search_pattern', 'tests/testfiles/long_file.txt:600:search_pattern', 'tests/testfiles/long_file.txt:601:search_pattern', 'tests/testfiles/long_file.txt:602:search_pattern', 'tests/testfiles/long_file.txt:603:search_pattern', 'tests/testfiles/long_file.txt:604:search_pattern', 'tests/testfiles/long_file.txt:605:search_pattern', 'tests/testfiles/long_file.txt:606:search_pattern', 'tests/testfiles/long_file.txt:607:search_pattern', 'tests/testfiles/long_file.txt:608:search_pattern', 'tests/testfiles/long_file.txt:609:search_pattern', 'tests/testfiles/long_file.txt:610:search_pattern', 'tests/testfiles/long_file.txt:611:search_pattern', 'tests/testfiles/long_file.txt:612:search_pattern', 'tests/testfiles/long_file.txt:613:search_pattern', 'tests/testfiles/long_file.txt:614:search_pattern', 'tests/testfiles/long_file.txt:615:search_pattern', 'tests/testfiles/long_file.txt:616:search_pattern', 'tests/testfiles/long_file.txt:617:search_pattern', 'tests/testfiles/long_file.txt:618:search_pattern', 'tests/testfiles/long_file.txt:619:search_pattern', 'tests/testfiles/long_file.txt:620:search_pattern', 'tests/testfiles/long_file.txt:621:search_pattern', 'tests/testfiles/long_file.txt:622:search_pattern', 'tests/testfiles/long_file.txt:623:search_pattern', 'tests/testfiles/long_file.txt:624:search_pattern', 'tests/testfiles/long_file.txt:625:search_pattern', 'tests/testfiles/long_file.txt:626:search_pattern', 'tests/testfiles/long_file.txt:627:search_pattern', 'tests/testfiles/long_file.txt:628:search_pattern', 'tests/testfiles/long_file.txt:629:search_pattern', 'tests/testfiles/long_file.txt:630:search_pattern', 'tests/testfiles/long_file.txt:631:search_pattern', 'tests/testfiles/long_file.txt:632:search_pattern', 'tests/testfiles/long_file.txt:633:search_pattern', 'tests/testfiles/long_file.txt:634:search_pattern', 'tests/testfiles/long_file.txt:635:search_pattern', 'tests/testfiles/long_file.txt:636:search_pattern', 'tests/testfiles/long_file.txt:637:search_pattern', 'tests/testfiles/long_file.txt:638:search_pattern', 'tests/testfiles/long_file.txt:639:search_pattern', 'tests/testfiles/long_file.txt:640:search_pattern', 'tests/testfiles/long_file.txt:641:search_pattern', 'tests/testfiles/long_file.txt:642:search_pattern', 'tests/testfiles/long_file.txt:643:search_pattern', 'tests/testfiles/long_file.txt:644:search_pattern', 'tests/testfiles/long_file.txt:645:search_pattern', 'tests/testfiles/long_file.txt:646:search_pattern', 'tests/testfiles/long_file.txt:647:search_pattern', 'tests/testfiles/long_file.txt:648:search_pattern', 'tests/testfiles/long_file.txt:649:search_pattern', 'tests/testfiles/long_file.txt:650:search_pattern', 'tests/testfiles/long_file.txt:651:search_pattern', 'tests/testfiles/long_file.txt:652:search_pattern', 'tests/testfiles/long_file.txt:653:search_pattern', 'tests/testfiles/long_file.txt:654:search_pattern', 'tests/testfiles/long_file.txt:655:search_pattern', 'tests/testfiles/long_file.txt:656:search_pattern', 'tests/testfiles/long_file.txt:657:search_pattern', 'tests/testfiles/long_file.txt:658:search_pattern', 'tests/testfiles/long_file.txt:659:search_pattern', 'tests/testfiles/long_file.txt:660:search_pattern', 'tests/testfiles/long_file.txt:661:search_pattern', 'tests/testfiles/long_file.txt:662:search_pattern', 'tests/testfiles/long_file.txt:663:search_pattern', 'tests/testfiles/long_file.txt:664:search_pattern', 'tests/testfiles/long_file.txt:665:search_pattern', 'tests/testfiles/long_file.txt:666:search_pattern', 'tests/testfiles/long_file.txt:667:search_pattern', 'tests/testfiles/long_file.txt:668:search_pattern', 'tests/testfiles/long_file.txt:669:search_pattern', 'tests/testfiles/long_file.txt:670:search_pattern', 'tests/testfiles/long_file.txt:671:search_pattern', 'tests/testfiles/long_file.txt:672:search_pattern', 'tests/testfiles/long_file.txt:673:search_pattern', 'tests/testfiles/long_file.txt:674:search_pattern', 'tests/testfiles/long_file.txt:675:search_pattern', 'tests/testfiles/long_file.txt:676:search_pattern', 'tests/testfiles/long_file.txt:677:search_pattern', 'tests/testfiles/long_file.txt:678:search_pattern', 'tests/testfiles/long_file.txt:679:search_pattern', 'tests/testfiles/long_file.txt:680:search_pattern', 'tests/testfiles/long_file.txt:681:search_pattern', 'tests/testfiles/long_file.txt:682:search_pattern', 'tests/testfiles/long_file.txt:683:search_pattern', 'tests/testfiles/long_file.txt:684:search_pattern', 'tests/testfiles/long_file.txt:685:search_pattern', 'tests/testfiles/long_file.txt:686:search_pattern', 'tests/testfiles/long_file.txt:687:search_pattern', 'tests/testfiles/long_file.txt:688:search_pattern', 'tests/testfiles/long_file.txt:689:search_pattern', 'tests/testfiles/long_file.txt:690:search_pattern', 'tests/testfiles/long_file.txt:691:search_pattern', 'tests/testfiles/long_file.txt:692:search_pattern', 'tests/testfiles/long_file.txt:693:search_pattern', 'tests/testfiles/long_file.txt:694:search_pattern', 'tests/testfiles/long_file.txt:695:search_pattern', 'tests/testfiles/long_file.txt:696:search_pattern', 'tests/testfiles/long_file.txt:697:search_pattern', 'tests/testfiles/long_file.txt:698:search_pattern', 'tests/testfiles/long_file.txt:699:search_pattern', 'tests/testfiles/long_file.txt:700:search_pattern', 'tests/testfiles/long_file.txt:701:search_pattern', 'tests/testfiles/long_file.txt:702:search_pattern', 'tests/testfiles/long_file.txt:703:search_pattern', 'tests/testfiles/long_file.txt:704:search_pattern', 'tests/testfiles/long_file.txt:705:search_pattern', 'tests/testfiles/long_file.txt:706:search_pattern', 'tests/testfiles/long_file.txt:707:search_pattern', 'tests/testfiles/long_file.txt:708:search_pattern', 'tests/testfiles/long_file.txt:709:search_pattern', 'tests/testfiles/long_file.txt:710:search_pattern', 'tests/testfiles/long_file.txt:711:search_pattern', 'tests/testfiles/long_file.txt:712:search_pattern', 'tests/testfiles/long_file.txt:713:search_pattern', 'tests/testfiles/long_file.txt:714:search_pattern', 'tests/testfiles/long_file.txt:715:search_pattern', 'tests/testfiles/long_file.txt:716:search_pattern', 'tests/testfiles/long_file.txt:717:search_pattern', 'tests/testfiles/long_file.txt:718:search_pattern', 'tests/testfiles/long_file.txt:719:search_pattern', 'tests/testfiles/long_file.txt:720:search_pattern', 'tests/testfiles/long_file.txt:721:search_pattern', 'tests/testfiles/long_file.txt:722:search_pattern', 'tests/testfiles/long_file.txt:723:search_pattern', 'tests/testfiles/long_file.txt:724:search_pattern', 'tests/testfiles/long_file.txt:725:search_pattern', 'tests/testfiles/long_file.txt:726:search_pattern', 'tests/testfiles/long_file.txt:727:search_pattern', 'tests/testfiles/long_file.txt:728:search_pattern', 'tests/testfiles/long_file.txt:729:search_pattern', 'tests/testfiles/long_file.txt:730:search_pattern', 'tests/testfiles/long_file.txt:731:search_pattern', 'tests/testfiles/long_file.txt:732:search_pattern', 'tests/testfiles/long_file.txt:733:search_pattern', 'tests/testfiles/long_file.txt:734:search_pattern', 'tests/testfiles/long_file.txt:735:search_pattern', 'tests/testfiles/long_file.txt:736:search_pattern', 'tests/testfiles/long_file.txt:737:search_pattern', 'tests/testfiles/long_file.txt:738:search_pattern', 'tests/testfiles/long_file.txt:739:search_pattern', 'tests/testfiles/long_file.txt:740:search_pattern', 'tests/testfiles/long_file.txt:741:search_pattern', 'tests/testfiles/long_file.txt:742:search_pattern', 'tests/testfiles/long_file.txt:743:search_pattern', 'tests/testfiles/long_file.txt:744:search_pattern', 'tests/testfiles/long_file.txt:745:search_pattern', 'tests/testfiles/long_file.txt:746:search_pattern', 'tests/testfiles/long_file.txt:747:search_pattern', 'tests/testfiles/long_file.txt:748:search_pattern', 'tests/testfiles/long_file.txt:749:search_pattern', 'tests/testfiles/long_file.txt:750:search_pattern', 'tests/testfiles/long_file.txt:751:search_pattern', 'tests/testfiles/long_file.txt:752:search_pattern', 'tests/testfiles/long_file.txt:753:search_pattern', 'tests/testfiles/long_file.txt:754:search_pattern', 'tests/testfiles/long_file.txt:755:search_pattern', 'tests/testfiles/long_file.txt:756:search_pattern', 'tests/testfiles/long_file.txt:757:search_pattern', 'tests/testfiles/long_file.txt:758:search_pattern', 'tests/testfiles/long_file.txt:759:search_pattern', 'tests/testfiles/long_file.txt:760:search_pattern', 'tests/testfiles/long_file.txt:761:search_pattern', 'tests/testfiles/long_file.txt:762:search_pattern', 'tests/testfiles/long_file.txt:763:search_pattern', 'tests/testfiles/long_file.txt:764:search_pattern', 'tests/testfiles/long_file.txt:765:search_pattern', 'tests/testfiles/long_file.txt:766:search_pattern', 'tests/testfiles/long_file.txt:767:search_pattern', 'tests/testfiles/long_file.txt:768:search_pattern', 'tests/testfiles/long_file.txt:769:search_pattern', 'tests/testfiles/long_file.txt:770:search_pattern', 'tests/testfiles/long_file.txt:771:search_pattern', 'tests/testfiles/long_file.txt:772:search_pattern', 'tests/testfiles/long_file.txt:773:search_pattern', 'tests/testfiles/long_file.txt:774:search_pattern', 'tests/testfiles/long_file.txt:775:search_pattern', 'tests/testfiles/long_file.txt:776:search_pattern', 'tests/testfiles/long_file.txt:777:search_pattern', 'tests/testfiles/long_file.txt:778:search_pattern', 'tests/testfiles/long_file.txt:779:search_pattern', 'tests/testfiles/long_file.txt:780:search_pattern', 'tests/testfiles/long_file.txt:781:search_pattern', 'tests/testfiles/long_file.txt:782:search_pattern', 'tests/testfiles/long_file.txt:783:search_pattern', 'tests/testfiles/long_file.txt:784:search_pattern', 'tests/testfiles/long_file.txt:785:search_pattern', 'tests/testfiles/long_file.txt:786:search_pattern', 'tests/testfiles/long_file.txt:787:search_pattern', 'tests/testfiles/long_file.txt:788:search_pattern', 'tests/testfiles/long_file.txt:789:search_pattern', 'tests/testfiles/long_file.txt:790:search_pattern', 'tests/testfiles/long_file.txt:791:search_pattern', 'tests/testfiles/long_file.txt:792:search_pattern', 'tests/testfiles/long_file.txt:793:search_pattern', 'tests/testfiles/long_file.txt:794:search_pattern', 'tests/testfiles/long_file.txt:795:search_pattern', 'tests/testfiles/long_file.txt:796:search_pattern', 'tests/testfiles/long_file.txt:797:search_pattern', 'tests/testfiles/long_file.txt:798:search_pattern', 'tests/testfiles/long_file.txt:799:search_pattern', 'tests/testfiles/long_file.txt:800:search_pattern', 'tests/testfiles/long_file.txt:801:search_pattern', 'tests/testfiles/long_file.txt:802:search_pattern', 'tests/testfiles/long_file.txt:803:search_pattern', 'tests/testfiles/long_file.txt:804:search_pattern', 'tests/testfiles/long_file.txt:805:search_pattern', 'tests/testfiles/long_file.txt:806:search_pattern', 'tests/testfiles/long_file.txt:807:search_pattern', 'tests/testfiles/long_file.txt:808:search_pattern', 'tests/testfiles/long_file.txt:809:search_pattern', 'tests/testfiles/long_file.txt:810:search_pattern', 'tests/testfiles/long_file.txt:811:search_pattern', 'tests/testfiles/long_file.txt:812:search_pattern', 'tests/testfiles/long_file.txt:813:search_pattern', 'tests/testfiles/long_file.txt:814:search_pattern', 'tests/testfiles/long_file.txt:815:search_pattern', 'tests/testfiles/long_file.txt:816:search_pattern', 'tests/testfiles/long_file.txt:817:search_pattern', 'tests/testfiles/long_file.txt:818:search_pattern', 'tests/testfiles/long_file.txt:819:search_pattern', 'tests/testfiles/long_file.txt:820:search_pattern', 'tests/testfiles/long_file.txt:821:search_pattern', 'tests/testfiles/long_file.txt:822:search_pattern', 'tests/testfiles/long_file.txt:823:search_pattern', 'tests/testfiles/long_file.txt:824:search_pattern', 'tests/testfiles/long_file.txt:825:search_pattern', 'tests/testfiles/long_file.txt:826:search_pattern', 'tests/testfiles/long_file.txt:827:search_pattern', 'tests/testfiles/long_file.txt:828:search_pattern', 'tests/testfiles/long_file.txt:829:search_pattern', 'tests/testfiles/long_file.txt:830:search_pattern', 'tests/testfiles/long_file.txt:831:search_pattern', 'tests/testfiles/long_file.txt:832:search_pattern', 'tests/testfiles/long_file.txt:833:search_pattern', 'tests/testfiles/long_file.txt:834:search_pattern', 'tests/testfiles/long_file.txt:835:search_pattern', 'tests/testfiles/long_file.txt:836:search_pattern', 'tests/testfiles/long_file.txt:837:search_pattern', 'tests/testfiles/long_file.txt:838:search_pattern', 'tests/testfiles/long_file.txt:839:search_pattern', 'tests/testfiles/long_file.txt:840:search_pattern', 'tests/testfiles/long_file.txt:841:search_pattern', 'tests/testfiles/long_file.txt:842:search_pattern', 'tests/testfiles/long_file.txt:843:search_pattern', 'tests/testfiles/long_file.txt:844:search_pattern', 'tests/testfiles/long_file.txt:845:search_pattern', 'tests/testfiles/long_file.txt:846:search_pattern', 'tests/testfiles/long_file.txt:847:search_pattern', 'tests/testfiles/long_file.txt:848:search_pattern', 'tests/testfiles/long_file.txt:849:search_pattern', 'tests/testfiles/long_file.txt:850:search_pattern', 'tests/testfiles/long_file.txt:851:search_pattern', 'tests/testfiles/long_file.txt:852:search_pattern', 'tests/testfiles/long_file.txt:853:search_pattern', 'tests/testfiles/long_file.txt:854:search_pattern', 'tests/testfiles/long_file.txt:855:search_pattern', 'tests/testfiles/long_file.txt:856:search_pattern', 'tests/testfiles/long_file.txt:857:search_pattern', 'tests/testfiles/long_file.txt:858:search_pattern', 'tests/testfiles/long_file.txt:859:search_pattern', 'tests/testfiles/long_file.txt:860:search_pattern', 'tests/testfiles/long_file.txt:861:search_pattern', 'tests/testfiles/long_file.txt:862:search_pattern', 'tests/testfiles/long_file.txt:863:search_pattern', 'tests/testfiles/long_file.txt:864:search_pattern', 'tests/testfiles/long_file.txt:865:search_pattern', 'tests/testfiles/long_file.txt:866:search_pattern', 'tests/testfiles/long_file.txt:867:search_pattern', 'tests/testfiles/long_file.txt:868:search_pattern', 'tests/testfiles/long_file.txt:869:search_pattern', 'tests/testfiles/long_file.txt:870:search_pattern', 'tests/testfiles/long_file.txt:871:search_pattern', 'tests/testfiles/long_file.txt:872:search_pattern', 'tests/testfiles/long_file.txt:873:search_pattern', 'tests/testfiles/long_file.txt:874:search_pattern', 'tests/testfiles/long_file.txt:875:search_pattern', 'tests/testfiles/long_file.txt:876:search_pattern', 'tests/testfiles/long_file.txt:877:search_pattern', 'tests/testfiles/long_file.txt:878:search_pattern', 'tests/testfiles/long_file.txt:879:search_pattern', 'tests/testfiles/long_file.txt:880:search_pattern', 'tests/testfiles/long_file.txt:881:search_pattern', 'tests/testfiles/long_file.txt:882:search_pattern', 'tests/testfiles/long_file.txt:883:search_pattern', 'tests/testfiles/long_file.txt:884:search_pattern', 'tests/testfiles/long_file.txt:885:search_pattern', 'tests/testfiles/long_file.txt:886:search_pattern', 'tests/testfiles/long_file.txt:887:search_pattern', 'tests/testfiles/long_file.txt:888:search_pattern', 'tests/testfiles/long_file.txt:889:search_pattern', 'tests/testfiles/long_file.txt:890:search_pattern', 'tests/testfiles/long_file.txt:891:search_pattern', 'tests/testfiles/long_file.txt:892:search_pattern', 'tests/testfiles/long_file.txt:893:search_pattern', 'tests/testfiles/long_file.txt:894:search_pattern', 'tests/testfiles/long_file.txt:895:search_pattern', 'tests/testfiles/long_file.txt:896:search_pattern', 'tests/testfiles/long_file.txt:897:search_pattern', 'tests/testfiles/long_file.txt:898:search_pattern', 'tests/testfiles/long_file.txt:899:search_pattern', 'tests/testfiles/long_file.txt:900:search_pattern', 'tests/testfiles/long_file.txt:901:search_pattern', 'tests/testfiles/long_file.txt:902:search_pattern', 'tests/testfiles/long_file.txt:903:search_pattern', 'tests/testfiles/long_file.txt:904:search_pattern', 'tests/testfiles/long_file.txt:905:search_pattern', 'tests/testfiles/long_file.txt:906:search_pattern', 'tests/testfiles/long_file.txt:907:search_pattern', 'tests/testfiles/long_file.txt:908:search_pattern', 'tests/testfiles/long_file.txt:909:search_pattern', 'tests/testfiles/long_file.txt:910:search_pattern', 'tests/testfiles/long_file.txt:911:search_pattern', 'tests/testfiles/long_file.txt:912:search_pattern', 'tests/testfiles/long_file.txt:913:search_pattern', 'tests/testfiles/long_file.txt:914:search_pattern', 'tests/testfiles/long_file.txt:915:search_pattern', 'tests/testfiles/long_file.txt:916:search_pattern', 'tests/testfiles/long_file.txt:917:search_pattern', 'tests/testfiles/long_file.txt:918:search_pattern', 'tests/testfiles/long_file.txt:919:search_pattern', 'tests/testfiles/long_file.txt:920:search_pattern', 'tests/testfiles/long_file.txt:921:search_pattern', 'tests/testfiles/long_file.txt:922:search_pattern', 'tests/testfiles/long_file.txt:923:search_pattern', 'tests/testfiles/long_file.txt:924:search_pattern', 'tests/testfiles/long_file.txt:925:search_pattern', 'tests/testfiles/long_file.txt:926:search_pattern', 'tests/testfiles/long_file.txt:927:search_pattern', 'tests/testfiles/long_file.txt:928:search_pattern', 'tests/testfiles/long_file.txt:929:search_pattern', 'tests/testfiles/long_file.txt:930:search_pattern', 'tests/testfiles/long_file.txt:931:search_pattern', 'tests/testfiles/long_file.txt:932:search_pattern', 'tests/testfiles/long_file.txt:933:search_pattern', 'tests/testfiles/long_file.txt:934:search_pattern', 'tests/testfiles/long_file.txt:935:search_pattern', 'tests/testfiles/long_file.txt:936:search_pattern', 'tests/testfiles/long_file.txt:937:search_pattern', 'tests/testfiles/long_file.txt:938:search_pattern', 'tests/testfiles/long_file.txt:939:search_pattern', 'tests/testfiles/long_file.txt:940:search_pattern', 'tests/testfiles/long_file.txt:941:search_pattern', 'tests/testfiles/long_file.txt:942:search_pattern', 'tests/testfiles/long_file.txt:943:search_pattern', 'tests/testfiles/long_file.txt:944:search_pattern', 'tests/testfiles/long_file.txt:945:search_pattern', 'tests/testfiles/long_file.txt:946:search_pattern', 'tests/testfiles/long_file.txt:947:search_pattern', 'tests/testfiles/long_file.txt:948:search_pattern', 'tests/testfiles/long_file.txt:949:search_pattern', 'tests/testfiles/long_file.txt:950:search_pattern', 'tests/testfiles/long_file.txt:951:search_pattern', 'tests/testfiles/long_file.txt:952:search_pattern', 'tests/testfiles/long_file.txt:953:search_pattern', 'tests/testfiles/long_file.txt:954:search_pattern', 'tests/testfiles/long_file.txt:955:search_pattern', 'tests/testfiles/long_file.txt:956:search_pattern', 'tests/testfiles/long_file.txt:957:search_pattern', 'tests/testfiles/long_file.txt:958:search_pattern', 'tests/testfiles/long_file.txt:959:search_pattern', 'tests/testfiles/long_file.txt:960:search_pattern', 'tests/testfiles/long_file.txt:961:search_pattern', 'tests/testfiles/long_file.txt:962:search_pattern', 'tests/testfiles/long_file.txt:963:search_pattern', 'tests/testfiles/long_file.txt:964:search_pattern', 'tests/testfiles/long_file.txt:965:search_pattern', 'tests/testfiles/long_file.txt:966:search_pattern', 'tests/testfiles/long_file.txt:967:search_pattern', 'tests/testfiles/long_file.txt:968:search_pattern', 'tests/testfiles/long_file.txt:969:search_pattern', 'tests/testfiles/long_file.txt:970:search_pattern', 'tests/testfiles/long_file.txt:971:search_pattern', 'tests/testfiles/long_file.txt:972:search_pattern', 'tests/testfiles/long_file.txt:973:search_pattern', 'tests/testfiles/long_file.txt:974:search_pattern', 'tests/testfiles/long_file.txt:975:search_pattern', 'tests/testfiles/long_file.txt:976:search_pattern', 'tests/testfiles/long_file.txt:977:search_pattern', 'tests/testfiles/long_file.txt:978:search_pattern', 'tests/testfiles/long_file.txt:979:search_pattern', 'tests/testfiles/long_file.txt:980:search_pattern', 'tests/testfiles/long_file.txt:981:search_pattern', 'tests/testfiles/long_file.txt:982:search_pattern', 'tests/testfiles/long_file.txt:983:search_pattern', 'tests/testfiles/long_file.txt:984:search_pattern', 'tests/testfiles/long_file.txt:985:search_pattern', 'tests/testfiles/long_file.txt:986:search_pattern', 'tests/testfiles/long_file.txt:987:search_pattern', 'tests/testfiles/long_file.txt:988:search_pattern', 'tests/testfiles/long_file.txt:989:search_pattern', 'tests/testfiles/long_file.txt:990:search_pattern', 'tests/testfiles/long_file.txt:991:search_pattern', 'tests/testfiles/long_file.txt:992:search_pattern', 'tests/testfiles/long_file.txt:993:search_pattern', 'tests/testfiles/long_file.txt:994:search_pattern', 'tests/testfiles/long_file.txt:995:search_pattern', 'tests/testfiles/long_file.txt:996:search_pattern', 'tests/testfiles/long_file.txt:997:search_pattern', 'tests/testfiles/long_file.txt:998:search_pattern', 'tests/testfiles/long_file.txt:999:search_pattern', 'tests/testfiles/long_file.txt:1000:search_pattern'], 'hint': 'Refine with a more specific pattern or a subdirectory path'}"
        )


class TestListDirectoryTool:
    def test_list_directory(self) -> None:
        dir_path: str = "tests/testfolder"
        result = list_directory.invoke({"path": dir_path})

        assert (
            result
            == "[('.venv', 'dir'), ('file1', 'file'), ('folder1', 'dir'), ('file2', 'file')]"
        )


class TestGetTools:
    def test_always_includes_builtins(self) -> None:
        tools = get_tools()
        names = {t.name for t in tools}
        assert "calculate" in names
        assert "get_current_datetime" in names

    def test_web_search_absent_without_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        tools = get_tools()
        names = {t.name for t in tools}
        assert "tavily_search_results_json" not in names


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestSettings:
    def test_default_provider_is_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("MODEL_NAME", "")
        s = get_settings()
        assert s.llm_provider == "openai"
        assert s.resolved_model == "gpt-5.4-nano"

    def test_anthropic_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("MODEL_NAME", "")
        s = get_settings()
        assert s.llm_provider == "anthropic"
        assert "claude" in s.resolved_model

    def test_ollama_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("MODEL_NAME", "")
        s = get_settings()
        assert s.llm_provider == "ollama"
        assert s.resolved_model == "qwen2.5-coder:14b"

    def test_ollama_base_url_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        s = get_settings()
        assert s.ollama_base_url == "http://localhost:11434"

    def test_explicit_model_name_overrides_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("MODEL_NAME", "gpt-5.4-mini")
        s = get_settings()
        assert s.resolved_model == "gpt-5.4-mini"

    def test_invalid_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "cohere")
        with pytest.raises(
            ValueError,
            match="Choose 'openai', 'anthropic', or 'ollama'",
        ):
            get_settings()


class TestGetLlmFactory:
    def test_openai_provider_branch(self) -> None:
        mock_chat_openai = MagicMock(name="ChatOpenAI")
        fake_module = types.SimpleNamespace(ChatOpenAI=mock_chat_openai)

        with patch.dict("sys.modules", {"langchain_openai": fake_module}):
            settings = Settings(
                llm_provider="openai",
                model_name="gpt-5.4-mini",
                temperature=0.3,
            )
            get_llm(settings)

        mock_chat_openai.assert_called_once_with(
            model="gpt-5.4-mini",
            temperature=0.3,
        )

    def test_anthropic_provider_branch(self) -> None:
        mock_chat_anthropic = MagicMock(name="ChatAnthropic")
        fake_module = types.SimpleNamespace(ChatAnthropic=mock_chat_anthropic)

        with patch.dict("sys.modules", {"langchain_anthropic": fake_module}):
            settings = Settings(
                llm_provider="anthropic",
                model_name="claude-haiku-4-5-20251001",
                temperature=0.1,
            )
            get_llm(settings)

        mock_chat_anthropic.assert_called_once_with(
            model="claude-haiku-4-5-20251001",
            temperature=0.1,
        )

    def test_ollama_provider_branch(self) -> None:
        mock_chat_ollama = MagicMock(name="ChatOllama")
        fake_module = types.SimpleNamespace(ChatOllama=mock_chat_ollama)

        with patch.dict("sys.modules", {"langchain_ollama": fake_module}):
            settings = Settings(
                llm_provider="ollama",
                model_name="qwen2.5-coder:14b",
                temperature=0.2,
                ollama_base_url="http://127.0.0.1:11434",
            )
            get_llm(settings)

        mock_chat_ollama.assert_called_once_with(
            model="qwen2.5-coder:14b",
            temperature=0.2,
            base_url="http://127.0.0.1:11434",
        )


# ---------------------------------------------------------------------------
# Graph structure tests — mock the LLM to avoid real API calls
# ---------------------------------------------------------------------------


class TestGraphStructure:
    def test_graph_compiles(self) -> None:
        """Graph should compile without errors (no API calls made)."""
        from agent.graph import build_graph

        g = build_graph()
        assert g is not None

    def test_graph_nodes(self) -> None:
        from agent.graph import build_graph

        g = build_graph()
        assert "agent" in g.nodes
        assert "tools" in g.nodes

    def test_graph_invoke_with_mock_llm(self) -> None:
        """Verify the full graph loop with a mocked LLM that returns immediately."""
        from agent.graph import build_graph
        from agent.nodes import _get_llm_with_tools

        # Create an AI message with NO tool calls → graph should go to END.
        mock_response = AIMessage(content="The answer is 42.")

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        with patch("agent.nodes._get_llm_with_tools", return_value=mock_llm):
            # Clear the cache so the patch takes effect.
            _get_llm_with_tools.cache_clear()
            g = build_graph()
            result = g.invoke(
                {"messages": [HumanMessage(content="What is 6 * 7?")]},
                config={"configurable": {"thread_id": "test"}},
            )

        messages = result["messages"]
        # The last message should be the AI response.
        assert isinstance(messages[-1], AIMessage)
        assert messages[-1].content == "The answer is 42."

    def test_system_prompt_prepended_once(self) -> None:
        """SystemMessage should be injected before the first HumanMessage."""
        from agent.nodes import call_model

        mock_response = AIMessage(content="Hello!")
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        with patch("agent.nodes._get_llm_with_tools", return_value=mock_llm):
            from agent.nodes import _get_llm_with_tools

            _get_llm_with_tools.cache_clear()
            state: AgentState = {"messages": [HumanMessage(content="Hi")]}
            call_model(state)

        call_args = mock_llm.invoke.call_args[0][0]
        assert isinstance(call_args[0], SystemMessage)


class TestCBashTool:
    def test_existing_parent_folder(self) -> None:
        command = "uname && ls"
        result = bash.invoke({"command": command})

        assert result.startswith("exit_code:")


# ---------------------------------------------------------------------------
# Tree-sitter tool tests — no LLM required
# ---------------------------------------------------------------------------


class TestTreeSitterTools:
    # -- treesitter_parse --

    def test_parse_python_file(self) -> None:
        """Parsing a real project file should return a JSON tree with a module root."""
        from agent.tools.tools_treesitter import treesitter_parse

        result = treesitter_parse.invoke({"path": "src/agent/tools/tools.py"})
        assert not result.startswith("Error:")
        # The output may be truncated for large files; check the opening JSON fragment.
        assert result.lstrip().startswith("{")
        assert '"type": "module"' in result
        assert '"start"' in result
        assert '"end"' in result

    def test_parse_inline_python_code(self) -> None:
        """Parsing an inline Python snippet should produce a function_definition
        node.
        """
        from agent.tools.tools_treesitter import treesitter_parse

        result = treesitter_parse.invoke(
            {"code": "def foo(): pass", "language": "python"}
        )
        assert not result.startswith("Error:")
        assert "function_definition" in result

    def test_parse_unsupported_language_returns_error(self) -> None:
        """An unknown language name must return an Error string."""
        from agent.tools.tools_treesitter import treesitter_parse

        result = treesitter_parse.invoke({"code": "test", "language": "cobol"})
        assert result.startswith("Error:")
        assert "cobol" in result

    def test_parse_path_outside_project_returns_error(self) -> None:
        """Paths outside the project root must be rejected."""
        from agent.tools.tools_treesitter import treesitter_parse

        result = treesitter_parse.invoke({"path": "/etc/passwd"})
        assert result.startswith("Error:")

    def test_parse_max_depth_respected(self) -> None:
        """Depth-0 parse should render the root as a leaf with a text field."""
        from agent.tools.tools_treesitter import treesitter_parse

        result = treesitter_parse.invoke(
            {"code": "x = 1", "language": "python", "max_depth": 0}
        )
        assert not result.startswith("Error:")
        data = json.loads(result)
        # At depth 0 no children should be expanded.
        assert "children" not in data
        assert "text" in data

    # -- treesitter_query --

    def test_query_captures_function_names(self) -> None:
        """A function-name query should return both defined function names."""
        from agent.tools.tools_treesitter import treesitter_query

        result = treesitter_query.invoke(
            {
                "query_pattern": "(function_definition name: (identifier) @fn_name)",
                "code": "def foo(): pass\ndef bar(): pass",
                "language": "python",
            }
        )
        assert not result.startswith("Error:")
        assert "foo" in result
        assert "bar" in result

    def test_query_on_file(self) -> None:
        """A query against a real file should return at least one match."""
        from agent.tools.tools_treesitter import treesitter_query

        result = treesitter_query.invoke(
            {
                "query_pattern": "(function_definition name: (identifier) @fn_name)",
                "path": "src/agent/tools/general.py",
            }
        )
        assert not result.startswith("Error:")
        assert "calculate" in result

    def test_query_invalid_pattern_returns_error(self) -> None:
        """A malformed query pattern must return an Error string."""
        from agent.tools.tools_treesitter import treesitter_query

        result = treesitter_query.invoke(
            {
                "query_pattern": "(((not_valid_syntax",
                "code": "def foo(): pass",
                "language": "python",
            }
        )
        assert result.startswith("Error:")

    # -- treesitter_get_symbols --

    def test_get_symbols_python_file(self) -> None:
        """Symbol extraction on a project Python file must include known functions."""
        from agent.tools.tools_treesitter import treesitter_get_symbols

        result = treesitter_get_symbols.invoke({"path": "src/agent/tools/general.py"})
        assert not result.startswith("Error:")
        assert "calculate" in result
        assert "get_current_datetime" in result

    def test_get_symbols_inline_rust(self) -> None:
        """Symbol extraction on inline Rust code must return a function entry."""
        from agent.tools.tools_treesitter import treesitter_get_symbols

        result = treesitter_get_symbols.invoke(
            {"code": 'fn main() { println!("hello"); }', "language": "rust"}
        )
        assert not result.startswith("Error:")
        assert "main" in result

    def test_get_symbols_no_query_for_language_returns_error(self) -> None:
        """A language with no pre-built symbol query must return an Error string."""
        from agent.tools.tools_treesitter import (
            _SYMBOL_QUERIES,
            treesitter_get_symbols,
        )

        # Temporarily remove a language from the symbol query map.
        original = _SYMBOL_QUERIES.pop("python", None)
        try:
            result = treesitter_get_symbols.invoke(
                {"code": "def foo(): pass", "language": "python"}
            )
            assert result.startswith("Error:")
            assert "python" in result
        finally:
            if original is not None:
                _SYMBOL_QUERIES["python"] = original

    def test_get_symbols_excludes_nested_symbols(self) -> None:
        """Symbols nested inside a function body must not appear in the result."""
        from agent.tools.tools_treesitter import treesitter_get_symbols

        code = "def outer():\n    def inner():\n        pass\n"
        result = treesitter_get_symbols.invoke({"code": code, "language": "python"})
        assert not result.startswith("Error:")
        symbols = json.loads(result)
        names = [s["name"] for s in symbols if isinstance(s, dict) and "name" in s]
        assert "outer" in names
        assert "inner" not in names

    def test_get_symbols_line_numbers_are_one_based(self) -> None:
        """start_line / end_line must use 1-based indexing."""
        from agent.tools.tools_treesitter import treesitter_get_symbols

        # Single function on lines 1-2 of the snippet.
        code = "def foo():\n    pass\n"
        result = treesitter_get_symbols.invoke({"code": code, "language": "python"})
        assert not result.startswith("Error:")
        symbols = json.loads(result)
        fn = next(s for s in symbols if s.get("name") == "foo")
        assert fn["start_line"] == 1
        assert fn["end_line"] == 2

    # -- get_tools integration --

    def test_get_tools_includes_treesitter(self) -> None:
        """All three tree-sitter tools must appear in the agent's tool list."""
        names = {t.name for t in get_tools()}
        assert "treesitter_parse" in names
        assert "treesitter_query" in names
        assert "treesitter_get_symbols" in names

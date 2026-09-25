from __future__ import annotations

import base64
import binascii
import re

from app.services.public_sql_statement_scanner import contains_runtime_sql
from app.services.public_sql_select_grammar import contains_public_select_sql
from app.services.public_sql_select_lexer import tokenize_select_copy


_SQL = re.compile(
    r"(?is)(?:"
    r"\bselect\b.{0,500}\bfrom\s+[a-z0-9_\"-]+(?:\.[a-z0-9_\"-]+)*"
    r"(?=\s*(?:;|$))|"
    r"\binsert\s+into\s+[a-z0-9_.\"-]+\s*(?:\(|default\s+values\b|"
    r"select\b|values\b)|"
    r"\bselect\s+(?:all\s+|distinct\s+)?"
    r"(?:[-+]?\d+(?:\.\d+)?|null|true|false|'[^']*'|\"[^\"]*\")"
    r"(?:\s*(?:[,;)]|$))|"
    r"\bvalues\s*\(\s*(?:[-+]?\d+(?:\.\d+)?|null|true|false|'[^']*'|"
    r"\"[^\"]*\"|[a-z_][a-z0-9_.]*)(?=\s*(?:,|\)))|"
    r"\bselect\s*(?:\(\s*[^)]+\)|array\s*\[[^]]+\])|"
    r"\bselect\s+(?:[-+]?\d+(?:\.\d+)?|'[^']*')"
    r"\s*::\s*[a-z0-9_.\"]+|"
    r"\bselect\s+[-+]?\d+(?:\s*[+*/%-]\s*[-+]?\d+)+|"
    r"\bselect\s+(?:all\s+|distinct\s+)?"
    r"[a-z_][a-z0-9_.$]*(?:\s*::\s*[a-z0-9_.\"]+)?\s*\(|"
    r"\bselect\s+(?:current_(?:catalog|date|role|schema|time(?:stamp)?|user)|"
    r"session_user|system_user|user)\b|"
    r"\bupdate\s+[a-z0-9_.\"-]+\s+set\s+[a-z0-9_.\"-]+\s*=|"
    r"\bdelete\s+from\s+[a-z0-9_\"-]+(?:\.[a-z0-9_\"-]+)*"
    r"(?=\s*(?:;|$|using\b|where\b|returning\b))|"
    r"\bmerge\s+into\b.{0,200}\busing\b|"
    r"\b(?:create|alter|drop)\s+"
    r"(?:access\s+method|aggregate|cast|collation|database|domain|event\s+trigger|"
    r"extension|foreign\s+data\s+wrapper|function|language|materialized\s+view|"
    r"operator|procedure|publication|rule|schema|secret|sequence|server|statistics|"
    r"subscription|tablespace|text\s+search\s+(?:configuration|dictionary|parser|"
    r"template)|transform|trigger|user\s+mapping|view)\b|"
    r"\bcreate\s+or\s+replace\s+(?:function|procedure|view)\b|"
    r"\bdrop\s+(?:foreign\s+)?table\b|"
    r"\balter\s+(?:foreign\s+)?table\s+[a-z0-9_.\"-]+\s+"
    r"(?:add|alter|attach|detach|disable|drop|enable|inherit|no\s+inherit|owner|"
    r"rename|replica|reset|set|validate)\b|"
    r"\bcreate\s+(?:(?:global|local)\s+)?(?:temp(?:orary)?\s+|unlogged\s+)?"
    r"(?:foreign\s+)?table\s+[a-z0-9_.\"-]+\s*(?:\(|as\b|like\b)|"
    r"\bcreate\s+(?:unique\s+)?index\s+[a-z0-9_.\"-]+.{0,120}\bon\b|"
    r"\b(?:create|alter)\s+type\s+[a-z0-9_.\"-]+\s+"
    r"(?:add|alter|as|owner|rename|set)\b|"
    r"\b(?:create|alter)\s+policy\s+[a-z0-9_.\"-]+\s+on\b|"
    r"\bdrop\s+policy\s+(?:if\s+exists\s+)?[a-z0-9_.\"-]+\s+on\b|"
    r"\bcreate\s+(?:extension|role|user)\s+(?!for\b)[a-z0-9_.\"-]+|"
    r"\bdrop\s+role\s+[a-z0-9_.\"-]+|"
    r"\balter\s+role\s+[a-z0-9_.\"-]+\s+"
    r"(?:bypassrls|createdb|createrole|encrypted|in|login|noinherit|nologin|"
    r"nosuperuser|password|rename|reset|set|superuser|valid|with)\b|"
    r"\balter\s+system\s+set\b|"
    r"\balter\s+default\s+privileges\b|"
    r"\bpragma\s+[a-z0-9_.\"-]+|"
    r"\battach\s+'[^']+'\s+as\s+[a-z0-9_.\"-]+|"
    r"\bsecurity\s+label\s+on\s+(?:column|function|schema|table|view)\b|"
    r"\bset\s+role\s+[a-z0-9_.\"-]+|\breset\s+all(?=\s*(?:;|$))|"
    r"\bshow\s+(?:all|time\s+zone)(?=\s*(?:;|$))|"
    r"\brefresh\s+materialized\s+view\s+[a-z0-9_.\"-]+(?=\s*(?:;|$))|"
    r"\btruncate\s+(?:table\s+)?[a-z0-9_.\"-]+|"
    r"\b(?:grant|revoke)\s+"
    r"(?:all|connect|create|delete|execute|insert|references|select|temporary|"
    r"trigger|truncate|update|usage)"
    r"(?:\s*,\s*(?:all|connect|create|delete|execute|insert|references|select|"
    r"temporary|trigger|truncate|update|usage))*\s+on\b|"
    r"\bgrant\s+[a-z0-9_.\"-]+(?:\s*,\s*[a-z0-9_.\"-]+)*"
    r"\s+to\s+[a-z0-9_.\"-]+|"
    r"\bcall\s+[a-z0-9_.\"-]+\s*\(|"
    r"\bcopy\s+[a-z0-9_.\"-]+\s+(?:from|to)\s+"
    r"(?:'|\(|program\b|std(?:in|out)\b)|"
    r"\bvacuum(?:\s+(?:analyze|freeze|full|verbose))*\s+[a-z0-9_.\"-]+|"
    r"\bwith\s+(?:recursive\s+)?[a-z0-9_.\"-]+\s+as\s*\(|"
    r"\banalyze(?:\s+verbose)?\s+[a-z0-9_.\"-]+(?=\s*(?:;|$))|"
    r"\bcomment\s+on\s+(?:column|function|index|table|view)\s+"
    r"[a-z0-9_.\"-]+\s+is\b|"
    r"\breindex\s+(?:database|index|schema|system|table)\b|"
    r"\b(?:set\s+search_path\s+(?:to|=)|show\s+[a-z0-9]+_[a-z0-9_]+)\b|"
    r"\b(?:begin|start)\s+(?:transaction|work)\b|"
    r"\bcommit\s+(?:transaction|work)\b|"
    r"\brollback(?:\s+(?:transaction|work))?\s+to\s+(?:savepoint\s+)?\w+|"
    r"\b(?:savepoint|release\s+savepoint)\s+\w+|"
    r"\bdo\s+\$[^$]*\$|\b(?:execute|deallocate)\s+\w+(?=\s*(?:\(|;|$))|"
    r"\bfetch\s+(?:next|prior|first|last|all|forward|backward)\b.{0,80}\bfrom\b|"
    r"\bclose\s+\w+(?=\s*(?:;|$))|"
    r"\bnotify\s+\w+(?=\s*(?:,|;|$))|"
    r"\b(?:describe|use|install)\s+\w+(?=\s*(?:;|$))"
    r")"
)
_SQL_STATEMENT = re.compile(
    r"(?is)^\s*(?:"
    r"select\b.+;|select\s+(?:all\s+|distinct\s+)?[a-z_][a-z0-9_.$\"]*|"
    r"(?:from|table)\s+[a-z0-9_.\"-]+\s*;?|"
    r"(?:abort|begin|commit|end|rollback)(?:\s+(?:transaction|work))?\s*;?|"
    r"checkpoint\s*;?|cluster\s+[a-z0-9_.\"-]+\s*;?|"
    r"(?:reset|show)\s+(?:all|search_path|server_version|statement_timeout|"
    r"time\s+zone|timezone)\s*;?|"
    r"set\s+(?:time\s+zone\s+.+|[a-z0-9_]+\s*(?:=|to)\s*.+)\s*;?|"
    r"discard\s+(?:all|plans|sequences|temporary|temp)\s*;?|"
    r"(?:listen\s+\w+|unlisten\s+(?:\*|\w+)|load\s+'[^']+')\s*;?|"
    r"prepare\s+(?:transaction\s+'[^']+'|\w+\s+as\s+.+)\s*;?|"
    r"(?:commit|rollback)\s+prepared\s+'[^']+'\s*;?|"
    r"import\s+foreign\s+schema\b.+|lock\s+table\b.+|"
    r"(?:drop|reassign)\s+owned\b.+|revoke\s+\w+\s+from\s+\w+\s*;?|"
    r"(?:create|drop)\s+macro\b.+|exec\s+\w+.*|"
    r"(?:export|import)\s+database\b.+|detach\s+\w+\s*;?|summarize\s+\w+.*|"
    r"(?:set|reset)\s+variable\b.+"
    r")\s*$"
)
_SQL_EXPRESSION_STATEMENT = re.compile(
    r"(?is)^\s*(?:"
    r"select\s+(?:"
    r"(?:true|false|null)\s+(?:and|or|is)\b.+|"
    r"case\b.+\bend\b|"
    r"(?:date|interval)\s+'[^']+'|"
    r".{0,200}\|\|.{1,200}|"
    r".{0,200}\bbetween\b.{1,100}\band\b.+|"
    r".{0,200}\bis\s+(?:not\s+)?null\b"
    r")|"
    r"load\s+(?:'[^']+'|[a-z0-9_.-]+)|"
    r"create\s+or\s+replace\s+macro\b.+|"
    r"create\s+(?:temp|temporary)\s+view\s+[a-z0-9_.\"-]+\s+"
    r"as\s+(?:table|select|values)\b.+|"
    r"explain\s+(?:\([^)]*\)\s+)*(?:analyze\s+)?"
    r"(?:select|from|table|values|with)\b.+|"
    r"create\s+or\s+replace\s+(?:(?:temp|temporary)\s+)?"
    r"(?:macro|sequence|table|view)\b.+|"
    r"create\s+or\s+replace\s+table\s+[a-z0-9_.\"-]+"
    r"(?:\s*\([^;]+\)|\s+as\s+(?:select|from|table|values|with)\b.+)|"
    r"create\s+or\s+replace\s+sequence\s+[a-z0-9_.\"-]+(?:\s+.+)?|"
    r"reset\s+[a-z0-9_.\"-]+|"
    r"(?:force\s+)?install\s+(?:'[^']+'|[a-z0-9_.\"-]+)"
    r"(?:\s+from\s+(?:'[^']+'|[a-z0-9_.\"-]+))?|"
    r"vacuum\s*\([^;)]+\)(?:\s+[a-z0-9_.\"-]+)?|"
    r"analyze(?:\s*\([^)]*\))?(?:\s+[a-z0-9_.\"-]+)?[.!?]?|"
    r"(?:abort|commit|end|rollback)(?:\s+(?:transaction|work))?"
    r"(?:\s+and\s+(?:no\s+)?chain)?[.!?]?|"
    r"begin(?:\s+(?:transaction|work))?(?:\s+(?:isolation\s+level\s+"
    r"(?:serializable|repeatable\s+read|read\s+committed|read\s+uncommitted)|"
    r"read\s+(?:only|write)|(?:not\s+)?deferrable))*[.!?]?|"
    r"cluster(?:\s+(?:verbose\s+)?[a-z0-9_.\"-]+)?[.!?]?|"
    r"vacuum|reset\s+role"
    r")\s*;?\s*$"
)
_SQL_ADMIN_STATEMENT = re.compile(
    r"(?is)^\s*(?:"
    r"drop\s+index\s+(?:concurrently\s+)?(?:if\s+exists\s+)?"
    r"[a-z0-9_.\"-]+(?:\s*,\s*[a-z0-9_.\"-]+)*(?:\s+(?:cascade|restrict))?|"
    r"alter\s+index\s+(?:if\s+exists\s+)?[a-z0-9_.\"-]+\s+"
    r"(?:attach\s+partition|depends\s+on\s+extension|no\s+depends\s+on\s+extension|"
    r"owner\s+to|rename\s+to|reset\s*\(|set\s*\().+|"
    r"drop\s+type\s+(?:if\s+exists\s+)?[a-z0-9_.\"-]+"
    r"(?:\s*,\s*[a-z0-9_.\"-]+)*(?:\s+(?:cascade|restrict))?|"
    r"alter\s+user\s+[a-z0-9_.\"-]+\s+(?:with\s+)?"
    r"(?:bypassrls|createdb|createrole|encrypted|in|login|noinherit|nologin|"
    r"nosuperuser|password|rename|reset|set|superuser|valid)\b.*|"
    r"drop\s+user\s+(?:if\s+exists\s+)?[a-z0-9_.\"-]+"
    r"(?:\s*,\s*[a-z0-9_.\"-]+)*|"
    r"create\s+or\s+replace\s+trigger\b.+|"
    r"create\s+conversion\s+[a-z0-9_.\"-]+\s+for\s+(?:'[^']+'|[a-z0-9_.\"-]+)"
    r"\s+to\s+(?:'[^']+'|[a-z0-9_.\"-]+)"
    r"\s+from\s+[a-z0-9_.\"-]+|"
    r"alter\s+conversion\s+[a-z0-9_.\"-]+\s+"
    r"(?:owner\s+to|rename\s+to|set\s+schema)\b.+|"
    r"drop\s+conversion\s+(?:if\s+exists\s+)?[a-z0-9_.\"-]+"
    r"(?:\s+(?:cascade|restrict))?|"
    r"create\s+group\s+[a-z0-9_.\"-]+(?:\s+with\b.+)?|"
    r"alter\s+group\s+[a-z0-9_.\"-]+\s+(?:add|drop)\s+user\b.+|"
    r"alter\s+group\s+[a-z0-9_.\"-]+\s+rename\s+to\b.+|"
    r"drop\s+group\s+(?:if\s+exists\s+)?[a-z0-9_.\"-]+|"
    r"(?:create|drop)\s+large\s+object\s+\d+|"
    r"alter\s+large\s+object\s+\d+\s+owner\s+to\b.+|"
    r"(?:alter|drop)\s+routine\s+[a-z0-9_.\"-]+(?:\s*\([^;]*\))?\s*.+|"
    r"declare\s+[a-z0-9_.\"-]+\s+.*cursor\b.*\bfor\s+"
    r"(?:select|table|with)\b.+|"
    r"move\s+(?:(?:absolute|relative)\s+[-+]?\d+|all|backward|first|forward|"
    r"last|next|prior)(?:\s+[-+]?\d+)?\s+(?:from|in)\s+[a-z0-9_.\"-]+|"
    r"reset\s+(?:role|session\s+authorization)|"
    r"set\s+(?:constraints\b.+\s+(?:deferred|immediate)|"
    r"session\s+authorization\b.+|(?:local\s+|session\s+)?transaction\b.+)|"
    r"copy\s*\(\s*(?:select|table|values|with)\b.+\)\s+to\s+"
    r"(?:stdout|program\b.+|'[^']+').*|"
    r"attach\s+(?:database\s+)?'[^']+'\s+as\s+[a-z0-9_.\"-]+|"
    r"detach\s+(?:database\s+)?[a-z0-9_.\"-]+|"
    r"(?:force\s+)?checkpoint(?:\s+[a-z0-9_.\"-]+)?"
    r")\s*;?\s*$"
)
_SQL_COMMENT = re.compile(r"(?s)/\*.*?\*/|--[^\r\n]*(?:\r\n?|\n|$)")
_BASE64_TEXT = re.compile(r"[A-Za-z0-9+/_-]{8,}={0,2}")


def _decoded_base64_text(value: str) -> str | None:
    if _BASE64_TEXT.fullmatch(value) is None or len(value) % 4 == 1:
        return None
    try:
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
        decoded = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if not decoded or any(not character.isprintable() for character in decoded):
        return None
    canonical = {
        base64.b64encode(raw).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(raw).decode("ascii").rstrip("="),
    }
    return decoded if value.rstrip("=") in canonical else None


def _contains_public_sql_form(value: str) -> bool:
    tokens = tokenize_select_copy(value)
    if tokens is not None and len(tokens) == 1 and tokens[0].kind == "string":
        return False

    comments = tuple(match.group(0) for match in _SQL_COMMENT.finditer(value))
    without_comments = _SQL_COMMENT.sub(" ", value)
    comment_bodies = (
        comment[2:-2] if comment.startswith("/*") else comment[2:]
        for comment in comments
    )
    return bool(
        contains_runtime_sql(value)
        or _SQL.search(value)
        or contains_public_select_sql(value)
        or _SQL_STATEMENT.search(value)
        or _SQL_EXPRESSION_STATEMENT.search(value)
        or _SQL_ADMIN_STATEMENT.search(value)
        or contains_runtime_sql(without_comments)
        or _SQL.search(without_comments)
        or contains_public_select_sql(without_comments)
        or _SQL_STATEMENT.search(without_comments)
        or _SQL_EXPRESSION_STATEMENT.search(without_comments)
        or _SQL_ADMIN_STATEMENT.search(without_comments)
        or any(
            contains_runtime_sql(body)
            or _SQL.search(body)
            or contains_public_select_sql(body)
            or _SQL_STATEMENT.search(body)
            or _SQL_EXPRESSION_STATEMENT.search(body)
            or _SQL_ADMIN_STATEMENT.search(body)
            for body in comment_bodies
        )
    )


def contains_public_sql(value: str) -> bool:

    decoded = _decoded_base64_text(value)
    return _contains_public_sql_form(value) or bool(
        decoded is not None and _contains_public_sql_form(decoded)
    )


__all__ = ("contains_public_sql",)

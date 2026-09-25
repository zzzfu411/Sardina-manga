"""Read Nuxt's literal state format without executing source JavaScript.

Only literals, function parameters, Array(length) placeholders, and assignments
into parameter dictionaries or arrays are accepted. Other calls, operators,
arbitrary statements and property reads are deliberately unsupported.
"""
from __future__ import annotations

import json
import re

IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")

class LiteralReader:
    def __init__(self, text, variables=None):
        if len(text) > 3_000_000:
            raise ValueError("源站状态数据过大")
        self.text, self.pos = text, 0
        self.variables = variables or {}
        self.nodes = 0

    def space(self):
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def take(self, token):
        self.space()
        if not self.text.startswith(token, self.pos):
            raise ValueError("源站状态格式已变化")
        self.pos += len(token)

    def identifier(self):
        self.space()
        match = IDENTIFIER.match(self.text, self.pos)
        if not match:
            raise ValueError("源站状态标识符无效")
        self.pos += len(match[0])
        return match[0]

    def value(self, depth=0):
        self.space()
        self.nodes += 1
        if depth > 80 or self.nodes > 150_000 or self.pos >= len(self.text):
            raise ValueError("源站状态数据超出限制")
        char = self.text[self.pos]
        if char == '"':
            value, end = json.JSONDecoder().raw_decode(self.text, self.pos)
            self.pos = end
            return value
        if char in "[{":
            is_list = char == "["
            closing = "]" if is_list else "}"
            result = [] if is_list else {}
            self.pos += 1
            self.space()
            if self.text.startswith(closing, self.pos):
                self.pos += 1
                return result
            while True:
                if is_list:
                    result.append(self.value(depth + 1))
                else:
                    self.space()
                    key = self.value(depth + 1) if self.text[self.pos] == '"' else self.identifier()
                    self.take(":")
                    result[key] = self.value(depth + 1)
                self.space()
                if self.text.startswith(closing, self.pos):
                    self.pos += 1
                    return result
                self.take(",")
        number = NUMBER.match(self.text, self.pos)
        if number:
            self.pos += len(number[0])
            return json.loads(number[0])
        name = self.identifier()
        if name == "Array":
            self.take("(")
            length = self.value(depth + 1)
            self.take(")")
            if type(length) is not int or not 0 <= length <= 20_000:
                raise ValueError("源站数组长度无效")
            self.nodes += length
            if self.nodes > 150_000:
                raise ValueError("源站状态数据超出限制")
            return [None] * length
        constants = {"true": True, "false": False, "null": None, "undefined": None}
        if name in constants:
            return constants[name]
        if name in self.variables:
            return self.variables[name]
        raise ValueError("源站包含不支持的状态表达式")

    def finish(self):
        self.space()
        if self.pos != len(self.text):
            raise ValueError("源站包含不支持的状态表达式")


def nuxt_state(page):
    """Decode a devalue-style IIFE; no eval, VM or external JS runtime."""
    script = re.search(r"window\.__NUXT__\s*=\s*(.*?);?</script>", page, re.S)
    if not script:
        raise ValueError("源站未返回漫画状态数据")
    expression = script[1].strip().rstrip(";")
    match = re.fullmatch(r"\(function\(([^)]*)\)\{(.*)\}\((.*)\)\)", expression, re.S)
    if not match:
        raise ValueError("源站状态封装格式已变化")
    names = match[1].split(",") if match[1] else []
    if any(not re.fullmatch(r"[A-Za-z_$][\w$]*", name) for name in names) or len(set(names)) != len(names):
        raise ValueError("源站状态参数无效")
    args = LiteralReader("[" + match[3] + "]")
    values = args.value()
    args.finish()
    if len(names) != len(values):
        raise ValueError("源站状态参数数量不匹配")
    reader = LiteralReader(match[2], dict(zip(names, values)))
    # Devalue populates shared array/object arguments before returning state.
    while True:
        name = reader.identifier()
        if name == "return":
            state = reader.value()
            reader.space()
            if reader.text.startswith(";", reader.pos):
                reader.pos += 1
            reader.finish()
            if not isinstance(state, dict):
                raise ValueError("源站漫画状态不是对象")
            return state
        target = reader.variables.get(name)
        reader.space()
        if reader.text.startswith(".", reader.pos) and isinstance(target, dict):
            reader.pos += 1
            key = reader.identifier()
        elif reader.text.startswith("[", reader.pos) and isinstance(target, (dict, list)):
            reader.pos += 1
            key = reader.value()
            reader.take("]")
        else:
            raise ValueError("源站包含不支持的状态赋值")
        reader.take("=")
        value = reader.value()
        reader.take(";")
        if isinstance(target, list):
            if type(key) is not int or not 0 <= key <= len(target) or key >= 20_000:
                raise ValueError("源站数组索引无效")
            if key == len(target):
                target.append(value)
            else:
                target[key] = value
        elif isinstance(key, str):
            target[key] = value
        else:
            raise ValueError("源站对象字段无效")

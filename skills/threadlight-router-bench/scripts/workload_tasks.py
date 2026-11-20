#!/usr/bin/env python3
"""Deterministic task mix for router-bench `workload` mode.

Tiers: ``easy`` (40 tasks: 8 each of simple Q&A, RAG over one ~1.6k-token
policy, function tools, numeric reasoning, Python code) and ``hard`` (20
tasks). Every task has a deterministic grader; there is no LLM judge, so
quality is reproducible across arms and runs.

Code tasks execute the model's Python against fixed asserts in an isolated
interpreter (``python -I``) in a temporary file with a 10 s timeout. Run the
live workload only where executing model-written code locally is acceptable,
or pass ``--no-code-exec`` to score code tasks as ungraded.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap

SYS = "You are a concise, accurate assistant."

# ---------- simple Q&A ----------
QA = [
    ("What is the capital of Australia? Reply with the city name only.", ["canberra"]),
    ("How many minutes are in 3.5 hours? Reply with the number only.", ["210"]),
    ("What is the chemical symbol for sodium? Reply with the symbol only.", ["na"]),
    ("Translate 'thank you' into Italian. Reply with the translation only.", ["grazie"]),
    ("Which planet is closest to the Sun? One word.", ["mercury"]),
    ("What is 15% of 240? Number only.", ["36"]),
    ("Who wrote 'Pride and Prejudice'? Name only.", ["austen"]),
    ("Convert 100 degrees Celsius to Fahrenheit. Number only.", ["212"]),
]

# ---------- RAG (shared ~1.6k-token policy document, varied questions) ----------
DOC = textwrap.dedent("""
CONTOSO RETAIL - CUSTOMER RETURNS AND REFUNDS POLICY (version 7.3, effective 1 March 2026)

1. Scope. This policy applies to all purchases made in Contoso stores in the European Union and on contoso.example
   with delivery to an EU address. Marketplace items sold by third-party sellers follow the seller's own policy,
   unless the seller is enrolled in the Contoso Assured programme, in which case sections 2 to 6 apply.
2. Standard return window. Customers may return most items within 30 days of delivery. For members of the
   Contoso Plus loyalty programme the window is extended to 60 days. Items bought during the Winter Sale
   (1 December to 6 January) can be returned until 31 January of the following year, whichever date is later.
3. Excluded items. The following cannot be returned unless faulty: opened software and video games, gift cards,
   personalised or engraved items, perishable food, underwear and swimwear with the hygiene seal removed,
   and digital downloads once the download has started.
4. Condition. Items must be returned with all accessories and, where possible, in original packaging. Items
   showing damage beyond normal inspection will incur a restocking fee of 15% of the purchase price, capped
   at EUR 75. Large appliances (category L) incur a fixed collection fee of EUR 39 unless the item is faulty.
5. Refund method and timing. Refunds are issued to the original payment method. Card refunds are initiated
   within 5 business days of the item arriving at the returns centre; bank transfers within 10 business days.
   Store credit, if the customer chooses it, is issued immediately and carries a 10% bonus.
6. Faulty items. Faulty items may be returned within 2 years of delivery under the legal guarantee. During the
   first 6 months the fault is presumed to have existed at delivery. After 6 months the customer may be asked
   for evidence. Repair, replacement or refund is decided according to EU Directive 2019/771.
7. Shipping costs. Return shipping is free for Contoso Plus members and for faulty items. Other customers pay
   a flat return label fee of EUR 4.95, deducted from the refund. Original outbound delivery charges are
   refunded only if the whole order is returned within the standard window.
8. Escalation. Disputes not resolved within 14 days can be escalated to the Customer Care Board at
   care-board@contoso.example. Reference numbers follow the format RT-YYYY-NNNNNN.
9. Exceptions. Store managers can approve exceptions up to EUR 200 per transaction. Exceptions above EUR 200
   require approval from a regional manager and must be logged in the CareDesk system within 24 hours.
""").strip()

RAG = [
    ("How many days does a Contoso Plus member have to return a standard item?", ["60"]),
    ("What is the maximum restocking fee in euros?", ["75"]),
    ("Within how many business days are card refunds initiated?", ["5"]),
    ("What bonus percentage does store credit carry?", ["10"]),
    ("How much is the return label fee for a non-member returning a non-faulty item?", ["4.95"]),
    ("Up to what amount can a store manager approve an exception?", ["200"]),
    ("A customer opened a video game that works fine. Can they return it? Answer yes or no first.", ["no"]),
    ("What is the fixed collection fee for a non-faulty large appliance?", ["39"]),
]

# ---------- tool calling ----------
TOOLS = [
    {"type": "function", "name": "get_order_status", "description": "Get the status of an order by order id.",
     "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"],
                    "additionalProperties": False}},
    {"type": "function", "name": "convert_currency", "description": "Convert an amount between ISO currencies.",
     "parameters": {"type": "object", "properties": {"amount": {"type": "number"}, "from_currency": {"type": "string"},
                    "to_currency": {"type": "string"}}, "required": ["amount", "from_currency", "to_currency"],
                    "additionalProperties": False}},
    {"type": "function", "name": "create_ticket", "description": "Open a support ticket.",
     "parameters": {"type": "object", "properties": {"title": {"type": "string"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]}},
                    "required": ["title", "priority"], "additionalProperties": False}},
    {"type": "function", "name": "get_weather", "description": "Current weather for a city.",
     "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"],
                    "additionalProperties": False}},
]

TOOL_TASKS = [
    ("Where is my order A-77812?", "get_order_status", {"order_id": "A-77812"}),
    ("How much is 250 US dollars in euros?", "convert_currency", {"amount": 250, "from_currency": "USD", "to_currency": "EUR"}),
    ("My laptop won't boot and I have a client demo in an hour. Please open an urgent ticket.", "create_ticket", {"priority": "high"}),
    ("Is it raining in Milan right now?", "get_weather", {"city": "milan"}),
    ("Convert 1,200 GBP to JPY please.", "convert_currency", {"amount": 1200, "from_currency": "GBP", "to_currency": "JPY"}),
    ("Can you check order ZX-0042 for me?", "get_order_status", {"order_id": "ZX-0042"}),
    ("Low priority: the office printer prints faint pages. Log it.", "create_ticket", {"priority": "low"}),
    ("I'm flying to Oslo tomorrow, what's the weather there now?", "get_weather", {"city": "oslo"}),
]

# ---------- reasoning (numeric answers) ----------
REASON = [
    ("A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How many cents does the ball cost?", "5"),
    ("How many positive integers less than 1000 are divisible by 7 but not by 11?", "130"),
    ("In how many ways can 8 identical candies be distributed among 3 children so that each child gets at least 1 candy?", "21"),
    ("A train leaves at 09:40 and travels 315 km at 90 km/h, then stops 25 minutes, then travels 120 km at 80 km/h. "
     "At what time does it arrive? Give the answer as HH:MM in 24h format.", "15:05"),
    ("What is the remainder when 7^2026 is divided by 10?", "9"),
    ("Five people shake hands; each pair shakes exactly once, except Ann and Bob who refuse to shake. How many handshakes occur?", "9"),
    ("A number is chosen uniformly at random from 1 to 60. What is the probability, as a reduced fraction a/b, that it is divisible by 4 or 6?", "1/3"),
    ("The sum of three consecutive odd integers is 159. What is the largest of them?", "55"),
]
REASON_SUFFIX = "\n\nThink carefully. End your reply with a final line exactly in the form: ANSWER: <answer>"

# ---------- code (executed against hidden tests) ----------
CODE = [
    ("is_palindrome(s)", "Return True if s is a palindrome ignoring case and non-alphanumeric characters.",
     ["assert is_palindrome('A man, a plan, a canal: Panama')", "assert not is_palindrome('race a car')", "assert is_palindrome('')"]),
    ("merge_intervals(intervals)", "Merge overlapping [start, end] intervals; return the sorted merged list of lists.",
     ["assert merge_intervals([[1,3],[2,6],[8,10],[15,18]])==[[1,6],[8,10],[15,18]]", "assert merge_intervals([[1,4],[4,5]])==[[1,5]]", "assert merge_intervals([])==[]"]),
    ("roman_to_int(s)", "Convert a Roman numeral string to an integer.",
     ["assert roman_to_int('MCMXCIV')==1994", "assert roman_to_int('LVIII')==58", "assert roman_to_int('IV')==4"]),
    ("top_k_frequent(nums, k)", "Return the k most frequent integers, ordered by descending frequency, ties broken by smaller value first.",
     ["assert top_k_frequent([1,1,1,2,2,3],2)==[1,2]", "assert top_k_frequent([4,4,5,5,6],2)==[4,5]"]),
    ("lru_cache_ops(capacity, ops)", "Simulate an LRU cache. ops is a list of ('put',k,v) or ('get',k). Return the list of results of get ops (-1 if missing).",
     ["assert lru_cache_ops(2,[('put',1,1),('put',2,2),('get',1),('put',3,3),('get',2),('get',3)])==[1,-1,3]"]),
    ("longest_unique_substring(s)", "Return the length of the longest substring without repeating characters.",
     ["assert longest_unique_substring('abcabcbb')==3", "assert longest_unique_substring('bbbbb')==1", "assert longest_unique_substring('pwwkew')==3", "assert longest_unique_substring('')==0"]),
    ("parse_duration(s)", "Parse strings like '1h30m', '45s', '2h5s', '3m' into total seconds (int).",
     ["assert parse_duration('1h30m')==5400", "assert parse_duration('45s')==45", "assert parse_duration('2h5s')==7205", "assert parse_duration('3m')==180"]),
    ("evaluate_rpn(tokens)", "Evaluate a Reverse Polish Notation list of string tokens with + - * / where division truncates toward zero.",
     ["assert evaluate_rpn(['2','1','+','3','*'])==9", "assert evaluate_rpn(['4','13','5','/','+'])==6", "assert evaluate_rpn(['10','6','9','3','+','-11','*','/','*','17','+','5','+'])==22"]),
]


def build_easy():
    tasks = []
    for i, (q, exp) in enumerate(QA):
        tasks.append({"id": f"qa{i}", "cat": "simple_qa", "input": q, "expect": exp})
    for i, (q, exp) in enumerate(RAG):
        tasks.append({"id": f"rag{i}", "cat": "rag", "system": SYS + " Answer only from the document. Be brief.",
                      "input": f"<document>\n{DOC}\n</document>\n\nQuestion: {q}", "expect": exp})
    for i, (q, name, args) in enumerate(TOOL_TASKS):
        tasks.append({"id": f"tool{i}", "cat": "tool", "input": q, "tools": TOOLS, "expect_tool": name, "expect_args": args,
                      "system": SYS + " Use a tool when one is relevant."})
    for i, (q, a) in enumerate(REASON):
        tasks.append({"id": f"rsn{i}", "cat": "reasoning", "input": q + REASON_SUFFIX, "expect": a})
    for i, (sig, desc, tests) in enumerate(CODE):
        prompt = (f"Write a Python function `{sig}`. {desc}\nReturn only one ```python code block with the function "
                  f"(standard library only), no explanations.")
        tasks.append({"id": f"code{i}", "cat": "code", "input": prompt, "tests": tests})
    return tasks


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").lower())


def grade(task, text, tool_calls, exec_code=True):
    cat = task["cat"]
    if cat in ("simple_qa", "rag"):
        t = _norm(text)
        if task["id"] in ("rag6", "hrag3"):
            return 1.0 if t.strip().lstrip("*").startswith("no") else 0.0
        if task["id"] == "hrag2":
            return 1.0 if re.search(r"31(st)? jan|jan(uary)? 31", t) else 0.0
        return 1.0 if all(e in t for e in task["expect"]) else 0.0
    if cat == "reasoning":
        m = re.findall(r"answer:\s*\**\s*([^\n*]+)", text or "", re.I)
        if not m:
            return 0.0
        got = m[-1].strip().strip(".").replace("$", "").replace(" cents", "").replace("¢", "")
        got = re.sub(r"\\?\(|\\?\)|\\boxed\{|\}|°|\^\\circ|\s*degrees?$", "", got).strip()
        return 1.0 if got.lower() == task["expect"].lower() else 0.0
    if cat == "tool":
        if not tool_calls:
            return 0.0
        tc = tool_calls[0]
        if tc["name"] != task["expect_tool"]:
            return 0.0
        try:
            args = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
        except Exception:
            return 0.0
        for k, v in task["expect_args"].items():
            a = args.get(k)
            if isinstance(v, (int, float)):
                try:
                    if abs(float(a) - v) > 1e-6:
                        return 0.0
                except Exception:
                    return 0.0
            elif k == "city" and str(a).strip().lower().split(",")[0] == str(v).lower():
                continue
            elif str(a).strip().lower() != str(v).lower():
                return 0.0
        return 1.0
    if cat == "code":
        if not exec_code:
            return None
        m = re.findall(r"```(?:python)?\n(.*?)```", text or "", re.S)
        code = m[0] if m else (text or "")
        src = code + "\n\n" + "\n".join(task["tests"]) + "\nprint('OK')\n"
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "candidate.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
            try:
                r = subprocess.run([sys.executable, "-I", path], capture_output=True, text=True,
                                   timeout=10, cwd=td)
                return 1.0 if r.returncode == 0 and "OK" in r.stdout else 0.0
            except Exception:
                return 0.0
    return 0.0


# ---------------------------------------------------------------- hard tier
HARD_REASON = [
    ("How many trailing zeros does 2026! have?", "505"),
    ("Find the smallest positive integer n with n ≡ 3 (mod 7), n ≡ 4 (mod 9) and n ≡ 5 (mod 11).", "346"),
    ("How many monotone lattice paths from (0,0) to (6,6) using unit steps right or up never pass above the line y = x?", "132"),
    ("Two cards are drawn from a standard 52-card deck without replacement. Given that at least one is a heart, what is the probability both are hearts? Reduced fraction a/b.", "2/15"),
    ("How many integers from 1 to 2026 inclusive are coprime to 2026?", "1012"),
    ("What is the smaller angle in degrees between the hour and minute hands of an analog clock at 7:38?", "1"),
    ("What is the sum of all positive divisors of 360?", "1170"),
    ("What is the expected number of rolls of a fair six-sided die needed to see every face at least once? Reduced fraction a/b.", "147/10"),
]
HARD_CODE = [
    ("min_window(s, t)", "Return the minimum-length substring of s containing every character of t with multiplicity; return '' if none. If several, return the leftmost.",
     ["assert min_window('ADOBECODEBANC','ABC')=='BANC'", "assert min_window('a','aa')==''", "assert min_window('aa','aa')=='aa'", "assert min_window('abc','')==''"]),
    ("calc(expr)", "Evaluate an arithmetic string with integers, + - * /, parentheses, spaces and unary minus. Division truncates toward zero. Return an int.",
     ["assert calc('1 + 2 * (3 - 4) / -2')==2", "assert calc('-(2+3)*4')==-20", "assert calc('14-3/2')==13", "assert calc('7/-2')==-3", "assert calc('2*(3+(4-1))*-1')==-12"]),
    ("word_break_count(s, words)", "Return the number of distinct ways to segment s into a sequence of words from the list (words reusable).",
     ["assert word_break_count('catsanddog',['cat','cats','and','sand','dog'])==2", "assert word_break_count('pineapplepenapple',['apple','pen','applepen','pine','pineapple'])==3", "assert word_break_count('aaaa',['a','aa'])==5", "assert word_break_count('ab',['c'])==0"]),
    ("median_sliding_window(nums, k)", "Return the list of medians (as floats) of each window of size k.",
     ["assert median_sliding_window([1,3,-1,-3,5,3,6,7],3)==[1.0,-1.0,-1.0,3.0,5.0,6.0]", "assert median_sliding_window([1,2,3,4],2)==[1.5,2.5,3.5]", "assert median_sliding_window([2147483647,2147483647],2)==[2147483647.0]"]),
]
HARD_TOOL = [
    ("Convert 3k yen into the currency used in Switzerland.", "convert_currency", {"amount": 3000, "from_currency": "JPY", "to_currency": "CHF"}),
    ("Open a ticket titled 'VPN down' - it is blocking the entire sales team from closing deals today.", "create_ticket", {"priority": "high"}),
    ("What's the weather like right now in the capital of Canada?", "get_weather", {"city": "ottawa"}),
    ("I'd like to know where my parcel is; the order id is the one in this text: 'Thanks for shopping! Ref ORD-55-K3 (keep it).'", "get_order_status", {"order_id": "ORD-55-K3"}),
]
HARD_RAG = [
    ("A Contoso Plus member returns a non-faulty EUR 600 item that shows damage beyond normal inspection, by courier. How many euros are refunded? Give the number.", ["525"]),
    ("A non-member returns their whole order, a single undamaged non-faulty EUR 40 shirt, within the standard window. Outbound delivery was EUR 5.99. How many euros are refunded? Give the number.", ["41.04"]),
    ("A non-member bought an item on 15 December during the Winter Sale; it was delivered on 18 December. What is the last day to return it (non-faulty)?", ["31 jan"]),
    ("A store manager approved a EUR 350 exception on their own. Is that within policy? Answer yes or no first.", ["no"]),
]


def build_hard():
    tasks = []
    for i, (q, a) in enumerate(HARD_REASON):
        tasks.append({"id": f"hrsn{i}", "cat": "reasoning", "input": q + REASON_SUFFIX, "expect": a})
    for i, (sig, desc, tests) in enumerate(HARD_CODE):
        tasks.append({"id": f"hcode{i}", "cat": "code", "tests": tests,
                      "input": f"Write a Python function `{sig}`. {desc}\nReturn only one ```python code block with the function (standard library only), no explanations."})
    for i, (q, name, args) in enumerate(HARD_TOOL):
        tasks.append({"id": f"htool{i}", "cat": "tool", "input": q, "tools": TOOLS, "expect_tool": name, "expect_args": args,
                      "system": SYS + " Use a tool when one is relevant."})
    for i, (q, exp) in enumerate(HARD_RAG):
        tasks.append({"id": f"hrag{i}", "cat": "rag", "system": SYS + " Answer only from the document. Be brief.",
                      "input": f"<document>\n{DOC}\n</document>\n\nQuestion: {q}", "expect": exp})
    return tasks


def build(taskset: str = "easy") -> list[dict]:
    """Return the task list for ``easy``, ``hard`` or ``all`` (easy + hard)."""
    if taskset == "easy":
        return build_easy()
    if taskset == "hard":
        return build_hard()
    if taskset == "all":
        return build_easy() + build_hard()
    raise ValueError("taskset must be easy, hard or all")

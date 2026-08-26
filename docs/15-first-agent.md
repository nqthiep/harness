# 15 — Your First Agent

> **What this document is.** This is not a description of the beginner tutorial. It *is*
> the beginner tutorial, written out in full, so the council's claim can be read and judged
> instead of taken on trust. It is the material used in SC-1b
> ([§14.2](14-validation-plan.md#2-sc-1--time-to-first-agent)), where three children aged
> 10–12 attempt it with no help.
>
> **Assumed knowledge:** `def`, variables, strings, lists, `print`, `import`, calling a
> function, and `pip install`. Nothing else. No decorators, no classes, no type hints, no
> environment variables, no `async`, no `try`/`except`.
>
> **Reviewers:** every sentence below is load-bearing. If a step needs an adult to explain
> it, that is an API bug (Round 16), not a writing bug.

---

# Make your own AI helper

You are going to build a robot helper that can answer questions. It takes about ten
minutes.

## Step 1 — Install it

Open your terminal and type this:

```
pip install harness
```

## Step 2 — Get your key

Your helper needs a key to talk to its brain. Think of it like a password.

Type this:

```
harness setup
```

It will ask you to paste a key, and it will tell you exactly where to get one. Then it
checks that the key works, so you know straight away if something went wrong.

> **Keep your key secret.** It is like a password for your account. Never put it in a
> message, and never upload it to the internet. Harness already helps with this — see the
> note in Step 3.

## Step 3 — Make your helper

Type this:

```
harness new joker
```

That makes a file called `joker.py`. Open it. It looks like this:

```python
from harness import Agent

# This is your helper. Change the words to make it do something else!
joker = Agent(
    name="Joker",
    job="Tell funny jokes for kids. Keep them short and silly.",
    budget="$0.05",     # It will never spend more than 5 cents on one answer.
)

print(joker.run("Tell me a joke about a cat"))
```

Three things make a helper:

| | |
|---|---|
| **name** | What it is called. |
| **job** | What you want it to do. Write it like you are telling a friend. |
| **budget** | The most money it is allowed to spend. It stops before it goes over. |

> `harness new` also made a file called `.gitignore`. That file keeps your secret key from
> being uploaded by accident. You do not have to do anything — it is already done.

## Step 4 — Run it

```
python joker.py
```

While it thinks, you will see it working:

```
Joker is thinking...
Joker is done.  ($0.002, 3s)

Why did the cat sit on the computer? To keep an eye on the mouse!
```

**You just built an AI helper.** 🎉

## Step 5 — Change its job

Open `joker.py` and change the `job` line:

```python
    job="Explain science to a 10 year old. Use fun examples.",
```

And change the question at the bottom:

```python
print(joker.run("Why is the sky blue?"))
```

Run it again. Same helper, completely different brain.

Try some more:

- `job="Help me practise Spanish. Reply in Spanish, then in English."`
- `job="You are a pirate. Answer everything like a pirate."`
- `job="Give me one interesting fact about animals."`

---

# Give your helper a superpower

Right now your helper can only talk. Let's give it something it can *do*.

Things a helper can do are called **tools**.

## A tool that comes with Harness

```python
from harness import Agent
from harness.tools.web import search          # ← new

researcher = Agent(
    name="Researcher",
    job="Answer questions using the web. Say where you found the answer.",
    tools=[search],                            # ← new
    budget="$0.05",
)

print(researcher.run("How tall is the tallest tree in the world?"))
```

Run it:

```
Researcher is thinking...
Researcher is searching the web...
Researcher is done.  ($0.009, 6s)

The tallest tree is Hyperion, a coast redwood in California. It is about
116 metres tall. (Source: nps.gov)
```

Now it can look things up.

## A tool you write yourself

This is the best part. A tool is **just a Python function** with one extra line above it.

```python
import random
from harness import Agent, tool

@tool(effect="read")
def roll_dice(sides: int) -> int:
    """Roll a dice and get a number."""
    return random.randint(1, sides)

gamemaster = Agent(
    name="Gamemaster",
    job="Play dice games with me. Roll the dice when I ask.",
    tools=[roll_dice],
    budget="$0.05",
)

print(gamemaster.run("Roll a 20 sided dice for me!"))
```

There are three new things in that function. Here is what each one is:

**1. `@tool(effect="read")`**

This line tells Harness "the function underneath is a tool". The `effect` part says what
the tool does in the world. There are four choices, and you pick one:

| write this | when your tool... | example |
|---|---|---|
| `effect="read"` | only **looks** at things | roll a dice, check the time, do maths |
| `effect="write"` | **changes** something, but you could undo it | save a file, update a score |
| `effect="external"` | brings in stuff from the **internet** | search the web, read a webpage |
| `effect="danger"` | does something you **can't undo** | send an email, delete a file, spend money |

Pick honestly. Harness uses your answer to keep you safe. If you say `danger`, it will
always ask you before it does it.

**2. `sides: int`**

This tells your helper what kind of thing to give the function. Your helper is not a
person, so it needs to be told.

| write this | it means |
|---|---|
| `int` | a whole number, like `7` |
| `float` | a number with a dot, like `2.5` |
| `str` | text, like `"hello"` |
| `bool` | yes or no (`True` or `False`) |

**3. `"""Roll a dice and get a number."""`**

This sentence tells your helper *when* to use the tool. Write it like you are explaining
it to someone. Your helper reads this to decide.

## Some tools to try

```python
@tool(effect="read")
def add_up(numbers: list) -> float:
    """Add a list of numbers together."""
    return sum(numbers)


@tool(effect="read")
def how_many_days_until(month: int, day: int) -> int:
    """How many days until a date this year."""
    import datetime
    today = datetime.date.today()
    target = datetime.date(today.year, month, day)
    return (target - today).days


@tool(effect="write")
def save_note(text: str) -> str:
    """Save a note so I can read it later."""
    with open("notes.txt", "a") as f:
        f.write(text + "\n")
    return "Saved!"
```

You can give your helper more than one:

```python
tools=[roll_dice, add_up, save_note],
```

---

# Talk to your helper

Instead of one question in a file, you can have a real conversation:

```
harness chat joker.py
```

```
You: hello!
Joker: Hi! Want to hear a joke about a robot?
You: yes please
Joker: Why did the robot go on holiday? It needed to recharge!
You: another one
Joker: ...
```

It remembers what you said before. Press Ctrl-C when you want to stop.

---

# When something goes wrong

Harness tries to tell you what to fix. Here are the messages you are most likely to see.

**You forgot to say what your tool does:**

```
Your tool needs to say what it does in the world.

    @tool(effect="danger")     ← probably this one, from the name
    def send_email(...):

  read      only looks at things
  write     changes something you could undo
  external  brings in stuff from the internet
  danger    does something you can't undo
```

**You forgot to say what kind of thing goes in:**

```
Your tool needs to say what kind of thing each answer is.

    def add(a, b):              ← you wrote this
    def add(a: int, b: int):    ← change it to this

  int = whole number   float = decimal   str = text   bool = yes/no
```

**You spelled an effect wrong:**

```
'reed' is not one of the four choices. Did you mean "read"?
```

**You forgot the labels:**

```
Agent needs you to label each part, like this:

    Agent(
        name="Helper",
        job="tell jokes",
    )

  You wrote:  Agent("Helper", "tell jokes")
```

**Your helper could look things up on the internet AND do something it can't undo:**

```
This helper can read things from the internet AND do something it can't undo.

  search      can bring in words from a website
  send_email  can't be undone

  A website could trick your helper into emailing your stuff to a stranger.

  Pick one:
    1. Take one of them out, or make two separate helpers.  ← easiest
    2. If send_email really is safe, say so on the tool:
         @tool(effect="danger", accepts_tainted=True)
```

This one stops **before** your helper does anything, so nothing bad can happen. It is not
you doing something wrong — it is Harness noticing that those two powers are risky
together.

**Your helper ran out of money:**

```
Joker stopped because it reached its budget of $0.05.
Here is what it managed to say:

    "Why did the cat sit on the..."

  To let it spend more, change:  budget="$0.20"
```

Every message tells you **what happened** and **exactly what to type instead**. If a
message ever leaves you stuck, that is our mistake — please tell us.

---

# What next

You now know everything you need to build helpers. When you want more, there is more:

| I want to... | Read |
|---|---|
| Make my helper remember things between runs | Memory guide |
| Ask before it does something risky | Safety guide |
| Make it cheaper | Cost guide |
| See exactly what it did | `harness trace` |
| Use it in a real program | [Developer docs](03-public-api.md) |

---
---

## Reviewer notes (not part of the tutorial)

**Concept count to a working agent:** three — `name`, `job`, `budget`. `budget` is included
in the scaffold rather than introduced later, because a child running a file eighty times
is a real cost event (G13.6), and showing the guard is cheaper than explaining it after the
fact.

**The `UnsafeToolSetError` entry was added in Round 17.** A child combining `search` with a
`danger` tool can reach it, and meeting an unannounced error is how a session ends. Every
error a child can reach must appear in this document.

**Concept count to a custom tool:** three more — the decorator line, one type hint, one
docstring. Each is introduced *after* the child has already seen it work, never before.

**Deliberate omissions.** No mention of: LLM, model, tokens, context, prompt, system
prompt, async, exception, class, object, attribute, environment variable, API, or JSON. A
child can complete every step above without meeting any of them. `.text` is never used
because `Result.__str__` returns the text (ADR-014).

**Where an adult is still needed.** Obtaining and pasting an API key in Step 2 — this
involves an account and, usually, a payment method. `harness setup` reduces it to one paste,
but it cannot remove the account. This is stated plainly rather than papered over, and it is
the one step where SC-1b permits an adult to act.

**The measured claim.** Not "this is simple". The claim is SC-1b: **≥ 2/3 children reach a
working agent in ≤ 20 minutes, and ≥ 2/3 add a tool of their own** — the second half being
the one that proves they built something rather than ran something.

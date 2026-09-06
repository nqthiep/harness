# 16 — SC-1b Field Kit

> **Why this document exists.** SC-1b is the one criterion in this package that code cannot
> close. Everything mechanically measurable about it now *is* measured — reading level,
> concept count, error-message conformance, the four-command cold start
> ([§14.1](14-validation-plan.md#1-traceability)). What remains needs three children, and
> a study nobody can run is not a gate, it is a wish.
>
> This is the runnable instrument: consent, script, observation sheet, scoring, and the
> analysis that turns the result into a decision. It is designed so a parent, teacher or
> engineer can run it in an afternoon without having read the rest of this package.
>
> **Correction, and a warning about what "mechanically measured" was worth.** The
> sentence above says everything mechanically measurable about SC-1b *is* measured, and
> that included "the four-command cold start". It was not: `harness` was not a command
> (no `[project.scripts]`), `harness setup` was advertised in `--help` with no branch
> behind it, and the key it would have written to `.env` was never read back — so Step 2
> of the script below, the step an adult performs while a child watches, could not have
> been completed by anyone. Found and fixed in ADR-086, now executed by
> `tests/test_m5.py::TheFourCommandColdStart`. The study was never run, so nobody hit
> it; had it been run, three children would have been stopped at the second command.

---

## 1. What is being measured

| | |
|---|---|
| **Question** | Can a ten-year-old who has taken a basic Python course build an agent, and give it a tool of their own? |
| **Participants** | 3 children, aged 10–12, who have completed an introductory Python course (they know `def`, variables, strings, lists, `print`, `import`, `pip install`) |
| **Materials** | [§15 — Your First Agent](15-first-agent.md). Nothing else. |
| **Environment** | A computer with Python already installed and a working terminal. Installing Python is not part of this library's claim and is excluded. |
| **Time** | 45 minutes maximum per child, including breaks |

### Pass thresholds

| Metric | Pass |
|---|---|
| Children reaching a working agent | **≥ 2 / 3, within 20 minutes** |
| **Children who add a tool of their own** | **≥ 2 / 3** |
| Children needing an adult to explain anything beyond Step 2 | **0** |
| Points where a child gave up and had to be restarted | **0** |

**Part 2 is the criterion that matters.** Running a provided example proves the example
works. Writing a tool is the point at which someone has built something — and it is the only
part of the ladder that touches `@tool`, type hints and `effect=`, the three concepts the
council argued hardest about.

**On failure: 1.0 is blocked and the council reconvenes on the API, not on this tutorial.**
If the proposed fix is "explain it better", the API is wrong. Written here in advance so the
result cannot be rationalized afterwards.

---

## 2. Before the session

- [ ] **Consent.** Written permission from a parent or guardian; verbal assent from the
      child, who is told they can stop at any moment for any reason and that **nothing they
      do can be wrong — we are testing the software, not them.**
- [ ] **No recording of the child.** Record the *screen* only, or take notes. No faces, no
      names in the notes — use "C1", "C2", "C3".
- [ ] **Set an account spend limit** before the session, at the provider. The child will run
      real calls. The `budget="$0.05"` in the scaffold caps each run; the account limit is
      the thing that cannot be edited away.
- [ ] **Do the API key step yourself, in advance** — see §3, Step 2. It requires an account
      and a payment method and is explicitly excluded from what is being measured.
- [ ] Have `python --version` ≥ 3.11 working, and a terminal already open.

---

## 3. Facilitator script

> Read the **bold** lines aloud. Everything else is instruction to you.
> **Do not explain, do not debug, do not point at the screen, do not type.**

**Opening.** *"You're going to make your own AI helper today. There's a page of
instructions — just follow it. I'm not allowed to help, because we're testing whether the
instructions are good enough. If you get stuck, that's useful information for us, not a
mistake by you. You can stop whenever you want."*

Hand over [§15](15-first-agent.md). Start the timer.

### What you may and may not do

| You MAY | You may NOT |
|---|---|
| Read a word aloud if asked what it says | Explain what a word means |
| Do the Step 2 account/key step (§3.1) | Type anything else |
| Say *"what does the page say?"* | Point at the screen |
| Say *"try what you think"* | Confirm whether a guess is right |
| Stop the session if the child is distressed | Debug an error |

### 3.1 The one step you perform

Step 2 (`harness setup`) needs an account and usually a payment method. **Do this part
yourself**, narrating only *"I'm putting in the password it needs."* Then hand back.

This is the single acknowledged gap in the child-facing path
([§08](08-poka-yoke.md), the "Documented" table) and it is excluded from the timing.

### 3.2 If the child stalls

Wait **two full minutes** of visible stuckness before intervening. Silence is data.

Then, in order, and record which rung you reached:

1. *"What does the page say to do next?"*
2. *"Is there anything on the screen that tells you what to try?"*
3. *"Would you like to skip this part?"* → mark the step **failed**, move to the next.

**Reaching rung 3 on any step means that step failed for that child.** Do not rescue it.

---

## 4. Observation sheet

One per child. Times are from the start of Step 1.

```
Child: C__      Age: __      Python course finished: ______      Date: __________

STEP                                        TIME    OK?   Notes / exact words used
 1  pip install harness                     ____    Y/N   ______________________
 2  harness setup            (adult does)    —      —     ______________________
 3  harness new joker                       ____    Y/N   ______________________
 4  python joker.py -> an answer            ____    Y/N   ______________________
    >>> MILESTONE A: a working agent        ____    Y/N
 5  changed the job, ran again              ____    Y/N   ______________________
 6  used a built-in tool (search)           ____    Y/N   ______________________
 7  WROTE THEIR OWN TOOL                    ____    Y/N   ______________________
    >>> MILESTONE B: added a tool           ____    Y/N

ERRORS THE CHILD HIT   (copy the first line, and what they did next)
  ______________________________________  ->  recovered alone / rung 1 / 2 / 3
  ______________________________________  ->  recovered alone / rung 1 / 2 / 3

EVERY QUESTION ASKED, VERBATIM
  ______________________________________________________________
  ______________________________________________________________

WHERE THEY LOOKED PUZZLED BUT DID NOT ASK
  ______________________________________________________________

STOPPED EARLY?   N / Y at step ___ because __________________________
```

**Record questions verbatim.** A paraphrase loses the vocabulary mismatch, which is usually
the actual finding.

---

## 5. Scoring

| | |
|---|---|
| **Milestone A** | The child ran an agent and saw an answer, unaided past Step 2 |
| **Milestone B** | The child wrote a `@tool` function of their own and the agent used it |
| **Unaided** | No rung-1/2/3 prompt was needed on that step |

```
                    C1     C2     C3        Pass needs
Milestone A       __/__  __/__  __/__        >= 2 of 3, each <= 20 min
Milestone B       __/__  __/__  __/__        >= 2 of 3
Adult explained   ____   ____   ____         0 across all children
Gave up           ____   ____   ____         0 across all children
```

---

## 6. Turning the result into a decision

**Every confusion point is mapped to a [§08](08-poka-yoke.md) register entry.** A confusion
with no matching entry means the register has a gap — add it, with the design-level defense,
before deciding anything else.

| Observation | What it means | Action |
|---|---|---|
| A child could not read a message aloud | Reading level regression | Failing check in `tests/test_m5.py` (`Readability`); fix the message |
| A child read it and still did not know what to do | The message names the problem but not the fix | Rewrite against the four-part standard ([§03.8](03-public-api.md#8-error-message-standard)) |
| A child did the right thing and it did not work | A real defect | File it; it is worth more than the rest of the study |
| A child needed a concept §15 never introduces | The ladder has a missing rung | **API change**, not a doc change |
| A child hit an error §15 does not show | Register gap | Add the message to §15 and to the conformance test |

**Report format** — three paragraphs, no more:

1. Did each threshold pass, with the numbers.
2. Every confusion point, verbatim, with its register entry or `NEW`.
3. The recommendation: ship, or reconvene the council on which specific part of the API.

---

## 7. What this kit cannot tell you

Stated so the result is not over-read.

- **n = 3.** This detects gross failures, not small differences. It cannot rank two designs.
- **The children are volunteers**, likely more motivated and more supported than average.
- **One sitting** measures first contact, not whether they would come back tomorrow.
- **The facilitator wants it to work.** That bias is why the script forbids explaining, why
  the stall rule is a full two minutes, and why questions are recorded verbatim.

A pass means *this did not fail badly for three children*. That is the honest claim, and it
is the one the council will record.

---

## 8. Already measured, and not part of this study

These were open questions until Round 31; they are now failing CI checks and need no child:

| Question | Answer | Where |
|---|---|---|
| Is the tutorial readable at age 10? | **Grade 4.2** (target ≤ 5.0) | `Readability` in `tests/test_m5.py` |
| Is every error a child can hit readable? | **Grade ≤ 4.9** — was 14.9 before Round 31 | same |
| Do the errors use internal vocabulary? | No — `parallel`, `retryable`, `untrusted`, `taint` are banned from prose | same |
| Does the code emit what §15 promises? | Whole-message comparison, not substrings | `TutorialPromises` |
| Does the first agent stay within three concepts? | `name`, `job`, `budget` | `ConceptBudget` |
| Do the four cold-start commands exist and work? | Yes | `Scaffold`, `Round30Promises` |
| Does every module §15 imports exist? | Yes — `harness.tools.web` was missing until Round 30 | `Round30Promises` |

**The study measures what is left: comprehension and behavior.**

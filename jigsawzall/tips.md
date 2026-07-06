# Working with Claude effectively — tips & ground rules

A living doc: how to hand Claude a long runway so you can set it going, walk away
(or sleep), and come back to real, verified progress — plus your standing edicts so
we get it right the first time.

---

## Part 1 — Giving Claude a long autonomous runway ("set it and sleep")

The dream: queue enough well-scoped work + enough agency that Claude keeps making
meaningful, correct progress unattended. What makes that work:

1. **Front-load a prioritized backlog.** Claude works a todo list. Before you leave,
   make sure there are several *independent*, well-scoped tasks queued (it tracks these
   internally; ask it to "show todos"). If it runs out, it stops. More queued work =
   longer productive runway. Order them; say which are AFK-safe vs need-your-input.

2. **Grant explicit agency, explicitly.** The magic phrase is roughly:
   > "I'm going AFK. Do all you can without prompting me. Make reasonable guesses when
   > you hit a decision, log the guess and why, and keep going until you're blocked or
   > out of todos. Keep a markdown progress log."
   Without this, Claude tends to stop and ask. With it, Claude will guess-and-proceed.

3. **Tell it how to handle ambiguity.** Default is to ask you. For AFK, say "guess +
   log, don't wait." For risky/irreversible calls, say "skip and flag for me" so it
   parks those instead of guessing.

4. **Demand a decision log.** Ask for a `PROGRESS_LOG.md` (git-ignored is fine). You get
   a reviewable trail of *what it did and why it chose each path* — far better than
   reverse-engineering a big diff. Claude did this for the jigsaw Phase 2 work.

5. **Set the risk envelope up front.** State what it may/may not do unattended:
   commit? (local yes / push no), delete files?, hit the network / external services?,
   spend on subagents? Ambiguity here makes Claude conservative (good) but slower.

6. **Give it a green-ness gate.** "Keep the test suite passing at every step; if a
   change can't be made green, revert it and log why." This prevents waking up to a
   broken tree. Claude will run tests after each change if told to keep them green.

7. **Checkpoint the work.** Ask for periodic WIP commits (local, no push) so progress is
   snapshotted and you can diff increments instead of one giant blob. (See edicts:
   push is never automatic.)

8. **Parallelize with subagents.** For research or wide searches, Claude can spawn
   background agents (we named one "the intern" for laser-raster research). They run
   while the main thread codes. Name them and give them a concrete deliverable (write a
   file + return a summary).

9. **Prefer visual/verifiable artifacts.** For anything visual (puzzle layouts, etch
   previews), have Claude *render an image and look at it* each iteration — it catches
   its own mistakes far better than by reading code/gcode. Ask it to keep a versioned
   image history so you can compare iterations.

10. **Timebox open-ended work.** "Audit the repo for X" can run forever. Say "spend up
    to N steps, then report findings" so it converges and reports instead of grinding.

### A reusable AFK kickoff template
> "AFK now. Backlog is the todo list (show it first). Do all you can unattended: make
> reasonable guesses, log each decision + rationale to PROGRESS_LOG.md, keep the tests
> green after every change, WIP-commit at each milestone (no push). For anything risky
> or irreversible, skip and flag it. Prefer rendering + eyeballing images over reading
> code. Work tasks in priority order; when the list is empty, stop and summarize. Don't
> wait on me for anything you can reasonably decide."

---

## Part 2 — Time & attention (a real limitation to plan around)

- **Claude has no wall clock.** It can't perceive elapsed real time, so "how long will
  this take?" is an inference from task *shape*, not a measurement — treat estimates as
  rough size classes (quick / medium / open-ended), not minutes.
- **Ask for a size warning.** Standing rule below: if a request will clearly run long,
  Claude should say so up front (one line) before diving in, so you can veto or narrow.
- **Big prompts are fine**, but batch related decisions. Changing direction mid-build is
  allowed and normal — just know each pivot costs some rework (e.g. the tab/seam design
  evolved several times; each redo was cheap but not free).

---

## Part 3 — Your standing edicts / mantras (checklist)

*(Seeded from how we've been working; edit freely — this is your list.)*

**Git & safety**
- [ ] **Never push** to any remote without an explicit ask. Local commits at milestones
      are fine.
- [ ] Commit only when it helps (checkpoints); don't fold my review flow into history
      unless asked. When you do commit, WIP messages are fine.
- [ ] Don't `git commit` in repos I've marked off-limits.
- [ ] Confirm before deleting/overwriting anything you didn't create.

**Communication**
- [ ] Show the todo list before starting a big prompt, and again after processing,
      before executing.
- [ ] Warn me if something will run much longer than a minute or two — I have a short
      attention span; a heads-up lets me decide to wait or narrow the ask.
- [ ] Relay outcomes honestly: if tests fail or a step was skipped, say so plainly.

**Workflow**
- [ ] Prefer PNG/SVG previews over raw g-code — I review visually.
- [ ] Keep a versioned image history (git-ignored) so old iterations aren't lost.
- [ ] Keep the test suite green; lock behavior with regression tests.
- [ ] Log decisions during unattended work.

**Laser / machine**
- [ ] Weak 10W diode: cut at **100% power** for all materials (calibration sweeps
      excepted). GRBL laser mode `$32=1` → the beam only fires while moving; G4 dwells
      do nothing, so cold-start fade is handled by motion (re-cutting the ramp region),
      never dwells.
- [ ] Fire risk on cardboard/wood: air assist, never leave unattended.

**Design taste (jigsaw, but generalizable)**
- [ ] Favor general, font/'input'-independent rules over hand-tuned lookup tables.
- [ ] Emergent counts (piece counts, etc.) bounded by tunable size limits beat
      hard-coded numbers.
- [ ] "Better to overcut than undercut" — conservative defaults.

---

## Part 4 — Prompt patterns that work well vs friction

**Works well**
- Decisive, specific asks ("make 1 and 2 wider; tabs at segment midpoints").
- "Make reasonable guesses and go" for exploratory work.
- Concrete acceptance criteria ("there should be a tab between each letter").
- Naming subagents + giving them a file to write.

**Creates friction**
- Ambiguous scope with no risk envelope (Claude stalls or over-asks).
- Interleaving many unrelated asks in one message without priorities (still works, but
  slower; a quick priority hint helps).
- Expecting time estimates in minutes (see Part 2).

---

*Add your own rules as we learn them. When something goes wrong, the fix usually belongs
here as a one-line edict so it doesn't recur.*

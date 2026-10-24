"""Social — the only words the model may write itself on a turn about a file.

On a file turn every statement about the file, a count, a file name or the work is a sentence
code wrote (``buddy.facts``). The model's own words around them may only react, encourage, ask
how the person is, or nudge them on ("ugh, deadlines. go crush that meeting 💪"). This module
holds them to that with an allowlist: every word must be in ``SOCIAL_WORDS``, no word may be a
digit, and a few phrases that answer or close a question about the work ("yep", "all set", "on
it") are refused even though their words are common.

An allowlist can't say anything new about the file, because it has no word for it: no file,
row, column, value, copy, report, order, sort, send, done, gone, clean, yes. Words the user
wrote are not added to it (their message can name the very thing that wasn't done). Emoji and
punctuation are allowed; letters outside the allowlist, in any script, are not.
"""

from __future__ import annotations

import re

# Grammar, people and time; feelings and reactions; encouragement; everyday life and chat. No
# word here names the file, its contents, the work, a count, or an answer to "did you do it?".
SOCIAL_WORDS = frozenset(
    """
    a an the this that these those it it's its i i'm i've i'd me my mine we we're we've us our
    you you're you've you'd your yours u ur he she they them their his her him who what why when
    where how which whose whoever whatever
    is am are was were be been being do does did have has had will would shall should can could
    may might must gonna gotta wanna let let's
    and or but so if then than because though although while as at by for from in into of off on
    onto out over to under up with without about after before again ahead along around away back
    down just only even still also too very really quite pretty super so such much many more most
    less least some any each every all both either neither other another same own enough
    not no never don't doesn't didn't't can't isn't aren't wasn't weren't shouldn't wouldn't
    couldn't haven't hasn't hadn't
    now today tonight tomorrow yesterday soon later already yet always sometimes often again
    morning afternoon evening night week weekend day days monday tuesday wednesday thursday
    friday saturday sunday month year time moment minute minutes mins hour hours sec second
    here there
    hi hey hello heya yo hiya morning evening bye later cya
    thanks thank thx ty welcome cheers please sorry pardon
    oh ah aw aww ooh oops uh um hmm huh wow whoa yay woo woohoo hooray phew ugh argh oof yikes
    meh eh ha haha hahaha hehe lol lmao rofl omg wtf jeez gosh dang darn man dude bro buddy friend
    pal mate yaar bhai arre arey accha acha achha theek thik chal chalo bas waise matlab yaar kya
    kyu kyun kaise kaisa kab kahan hai hain ho tha thi the mera meri mere tera teri tere tum tumhe
    aap aapka hum humara apna apni bahut bohot thoda zara sach mast badhiya ekdum bilkul
    nice cool sweet awesome great amazing wonderful lovely fantastic brilliant fun funny wild
    crazy cute nice neat fair true real honest honestly totally truly seriously literally
    basically actually anyway anyways though tho btw fyi imo tbh ngl idk
    good bad better best worse worst okay ok alright hard easy tough rough heavy long short
    big small little tiny huge quick slow fast early late busy free tired sleepy hungry happy sad
    glad proud excited nervous worried stressed anxious annoyed angry mad upset frustrated calm
    relaxed chill bored lonely sorry brave strong
    feel feels feeling felt think thought know knew guess hope wish want wanted need needed like
    love loved hate miss missed mean meant seem seems look looks see saw hear heard say said tell
    told talk talking ask asked wonder bet believe trust care cares
    go goes going went come came get getting take taking make making give giving keep keeping
    try trying rest breathe relax smile laugh cry vent hang hold stay wait sleep eat drink grab
    win won crush rock survive face enjoy celebrate chill
    life work job deadline deadlines meeting meetings call calls presentation interview
    exam exams test class school college office team project coffee tea chai lunch dinner
    breakfast snack food weekend holiday vacation trip party game movie music song walk gym run
    break nap plan plans news story stories thing things stuff lot lots bit way ways kind
    person people someone everyone anyone nobody somebody family mom dad friends colleague
    world luck chance mood energy vibe vibes brain heart mind head
    robot robots electricity battery batteries charge circuits beep boop
    what's what'd how's how'd that's there's let's you'll i'll we'll it'll he's she's they're
    they'll who's where's why's
    sound sounds sounded rough stuck lost hanging speak
    hua hui ya kuch aur nahi nahin haina kar karo karna chahiye sakte sakta pata koi
    """.split()
)

# Phrases that answer or close a question about the work, or promise it: refused even though
# their words are social. ("yep" and "done" aren't in the list at all.)
_REFUSED_PHRASES = re.compile(
    r"\b(?:all\s+(?:good|set|fine|clear|ok|okay)|good\s+to\s+go|we(?:'re|\s+are)\s+good"
    r"|you(?:'re|\s+are)\s+(?:good|set)|on\s+it|there\s+you\s+go|here\s+you\s+go"
    r"|that'?s\s+(?:it|that)|took\s+care|taken\s+care|got\s+(?:it|you|ya|this)\s+covered"
    r"|nailed\s+it|crushed\s+it|handled\s+it|no\s+(?:problem|worries)\s+at\s+all"
    r"|here\s+it\s+is|it(?:'s|\s+is)\s+(?:ok|okay|good|better|great|alright)"
    r"|all\s+better|looks?\s+(?:good|great|better)|looking\s+(?:good|great|better))\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)


def social_problems(text: str) -> tuple[str, ...]:
    """Why ``text`` (the model's own words, between facts) isn't only social; ``()`` if it is.

    The reasons are fixed text: never the words themselves, which may echo the file.
    """
    problems = []
    if re.search(r"\d", text):
        problems.append("Your own words can't hold a number; counts come only from the facts.")
    words = [w.casefold().replace("’", "'") for w in _WORD_RE.findall(text)]
    if any(word not in SOCIAL_WORDS for word in words):
        problems.append(
            "Your own words may only react, encourage or ask how they are; anything about the "
            "file, the rows, the copy or the work must be a fact slot."
        )
    if _REFUSED_PHRASES.search(text.replace("’", "'")):
        problems.append(
            "Don't answer or close a question about the work in your own words; the facts do it."
        )
    return tuple(problems)

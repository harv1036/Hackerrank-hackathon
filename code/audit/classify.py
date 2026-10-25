"""Decide whether a request actually authorises deletion.

Stage 1 is a deterministic multilingual rule set.  It returns DELETE / NOT_DELETE with a confidence flag.
Stage 2 (optional, only when stage 1 is unsure and GEMINI_API_KEY is set) asks a small LLM, see llm.py.
"""
import re

DELETE_WORDS = re.compile(
    r"\b(delet\w*|eras\w*|remov\w*|clos\w*|wip\w*|purg\w*|forgotten|forget me|destroy\w*|get rid|take down|shut down|"
    r"deactivat\w*|terminat\w*|cancel\w*|gone|no longer exist\w*|still exist\w*|expunge\w*)\b"
    r"|supprim\w*|suppression|effac\w*|clôtur\w*|fermer|ferme\b|"
    r"eliminaci\w*|elimin\w*|borrar\w*|borrad\w*|cierr\w*|"
    r"exclus\w*|exclu[ií]\w*|apag\w*|encerr\w*|"
    r"lösch\w*|entfern\w*|"
    r"cancell\w*|rimuov\w*|eliminare|chiudere|"
    r"삭제|탈퇴|지워|"
    r"削除|消去|退会|"
    r"删除|注销|刪除|"
    r"удал\w*|"
    r"حذف|إلغاء",
    re.IGNORECASE)

# the person explicitly does NOT want the account or records touched
NEGATED = re.compile(
    r"not (?:asking|requesting|looking)(?: for)?(?: a)?(?: account)? (?:deletion|erasure|to delete|to erase|to remove|to close)"
    r"|not a request (?:to|for) (?:delete|erase|remove|close|deletion|erasure)"
    r"|(?:do not|don't|dont|please do not|please don't|never) (?:delete|erase|remove|close|wipe|touch)"
    r"|do not close|don't close"
    r"|(?:leave|keep) (?:the |my |this )?(?:account|profile|records?|data|history)"
    r"|(?:leave|keep) (?:my )?(?:profile|account) (?:and|active|as it is|in place)"
    r"|without (?:removing|deleting|erasing|closing)"
    r"|still (?:use|need|want|have)"
    r"|(?:continue|keep) using"
    r"|(?:i|we) (?:want|wish|need) to (?:keep|continue)"
    r"|only (?:from )?(?:the )?marketing|marketing (?:lists |emails )?only|lists only"
    r"|(?:no|not) (?:deletion|erasure)",
    re.IGNORECASE)

NON_DELETE_INTENT = re.compile(
    r"\b(download your data|download my|download (?:a |the )?(?:copy|my account)|portable format|what personal information|"
    r"tell me what|send me a copy of (?:the |my )?(?:information|data|invoices)|copy of (?:the information|last year)|"
    r"access request|misspell\w*|correct (?:my|the|this) (?:name|surname|address|email|phone)|"
    r"update my (?:name|phone|email|address)|change (?:the |my )?(?:email|phone|address|name)|replace it|"
    r"reset(?:ting)? (?:my|the) (?:login|password)|unsubscribe|stop (?:the )?(?:promotional|marketing)|opt[- ]?out)\b",
    re.IGNORECASE)


def rule_classify(text):
    """Return (label, confident). label is 'DELETE' or 'NOT_DELETE'; confident=False asks for the LLM."""
    t = (text or '').strip()
    has_delete = bool(DELETE_WORDS.search(t))
    negated = bool(NEGATED.search(t))
    other = bool(NON_DELETE_INTENT.search(t))
    if not has_delete:
        # no deletion verb in a language the lexicon knows: unsure when the text is in an unknown script
        return 'NOT_DELETE', not re.search(r'[^\x00-\x7f]', t)
    if negated:
        return 'NOT_DELETE', True
    if other:
        return 'DELETE', False          # a deletion verb next to access/correction/marketing wording
    return 'DELETE', True


def classify(text, llm=None):
    label, sure = rule_classify(text)
    if not sure and llm is not None:
        out = llm.classify_request(text)
        if out in ('DELETE', 'NOT_DELETE'):
            return out, 'llm'
    return label, 'rules'

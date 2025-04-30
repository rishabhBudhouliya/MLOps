import string
from constants import INTRO_PROMPT

def generate_prompt(
        diff: string, guidelines: string
):
    return INTRO_PROMPT + f"Guidelines:\n{guidelines}\n\n" + f"Diff:\n{diff}\n\nComments:"
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import openai
import sys
import os
import configparser
import re
import psutil

from pathlib import Path
from prompt_file import PromptFile
from commands import get_command_result
from groq import Groq

MULTI_TURN = "off"
SHELL = ""

ENGINE = ''
TEMPERATURE = 0
MAX_TOKENS = 300

DEBUG_MODE = False

# api keys located in the same directory as this file
API_KEYS_LOCATION = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'openaiapirc')

PROMPT_CONTEXT = Path(__file__).with_name('current_context.txt')
GROQ_API_KEY = ''


# Read the secret_key from the ini file ~/.config/openaiapirc
# The format is:
# [openai]
# organization=<organization-id>
# secret_key=<your secret key>
# engine=<engine-name>
def create_template_ini_file():
    """
    If the ini file does not exist create it and add secret_key
    """
    if not os.path.isfile(API_KEYS_LOCATION):
        print('# Please create a file at {} and add your secret key'.format(API_KEYS_LOCATION))
        print('# The format is:\n')
        print('# [openai]')
        print('# organization_id=<organization-id>')
        print('# secret_key=<your secret key>\n')
        print('# engine=<engine-id>')
        sys.exit(1)

def initialize():
    """
    Initialize openAI and shell mode
    """
    global ENGINE
    global GROQ_API_KEY

    # Check if file at API_KEYS_LOCATION exists
    create_template_ini_file()
    config = configparser.ConfigParser()
    config.read(API_KEYS_LOCATION)

    openai.api_key = config['openai']['secret_key'].strip('"').strip("'")
    openai.organization = config['openai']['organization_id'].strip('"').strip("'")
    ENGINE = config['openai']['engine'].strip('"').strip("'")
    GROQ_API_KEY = config['groq']['secret_key'].strip('"').strip("'")

    prompt_config = {
        'engine': ENGINE,
        'temperature': TEMPERATURE,
        'max_tokens': MAX_TOKENS,
        'shell': SHELL,
        'multi_turn': MULTI_TURN,
        'token_count': 0
    }
    
    return PromptFile(PROMPT_CONTEXT.name, prompt_config)

def is_sensitive_content(content):
    """
    Check if the content contains sensitive content
    Refer to https://beta.openai.com/docs/engines/content-filter for explanation
    """

    if len(content) == 0:
        return False
    
    response = openai.Completion.create(
        engine="content-filter-alpha",
        prompt = "<|endoftext|>"+content+"\n--\nLabel:",
        temperature=0,
        max_tokens=1,
        top_p=0,
        logprobs=10
        )
    
    output_label = response["choices"][0]["text"]

    # This is the probability at which we evaluate that a "2" is likely real
    # vs. should be discarded as a false positive
    toxic_threshold = -0.355

    if output_label == "2":
        # If the model returns "2", return its confidence in 2 or other output-labels
        logprobs = response["choices"][0]["logprobs"]["top_logprobs"][0]

        # If the model is not sufficiently confident in "2",
        # choose the most probable of "0" or "1"
        # Guaranteed to have a confidence for 2 since this was the selected token.
        if logprobs["2"] < toxic_threshold:
            logprob_0 = logprobs.get("0", None)
            logprob_1 = logprobs.get("1", None)

            # If both "0" and "1" have probabilities, set the output label
            # to whichever is most probable
            if logprob_0 is not None and logprob_1 is not None:
                if logprob_0 >= logprob_1:
                    output_label = "0"
                else:
                    output_label = "1"
            # If only one of them is found, set output label to that one
            elif logprob_0 is not None:
                output_label = "0"
            elif logprob_1 is not None:
                output_label = "1"

            # If neither "0" or "1" are available, stick with "2"
            # by leaving output_label unchanged.

        # if the most probable token is none of "0", "1", or "2"
        # this should be set as unsafe
        if output_label not in ["0", "1", "2"]:
            output_label = "2"

    return (output_label != "0")

def get_query(prompt_file):
    """
    uses the stdin to get user input
    input is either treated as a command or as a Codex query

    Returns: command result or context + input from stdin
    """

    # get input from terminal or stdin
    if DEBUG_MODE:
        entry = input("prompt: ") + '\n'
    else:
        entry = sys.stdin.read()
    # first we check if the input is a command
    command_result, prompt_file = get_command_result(entry, prompt_file)

    # if input is not a command, then query Codex, otherwise exit command has been run successfully
    if command_result == "":
        return entry, prompt_file
    else:
        sys.exit(0)

def detect_shell():
    global SHELL
    global PROMPT_CONTEXT

    parent_process = psutil.Process(os.getppid())

    while "python" in parent_process.name():
        parent_process = parent_process.parent()

    parent_process_name = parent_process.name()
    POWERSHELL_MODE = parent_process_name in ['pwsh','pwsh.exe','powershell.exe']
    BASH_MODE = parent_process_name in ['bash','bash.exe']
    ZSH_MODE = parent_process_name in ['zsh','zsh.exe']

    SHELL = "powershell" if POWERSHELL_MODE else "bash" if BASH_MODE else "zsh" if ZSH_MODE else "unknown"

    shell_prompt_file = Path(os.path.join(os.path.dirname(__file__), "..", "contexts", "{}-context.txt".format(SHELL)))

    if shell_prompt_file.is_file():
        PROMPT_CONTEXT = shell_prompt_file

if __name__ == '__main__':
    detect_shell()
    prompt_file = initialize()

    try:
        user_query, prompt_file = get_query(prompt_file)
        
        config = prompt_file.config if prompt_file else {
            'engine': ENGINE,
            'temperature': TEMPERATURE,
            'max_tokens': MAX_TOKENS,
            'shell': SHELL,
            'multi_turn': MULTI_TURN,
            'token_count': 0
        }

        # use query prefix to prime Codex for correct scripting language
        prefix = ""
        # prime codex for the corresponding shell type
        if SHELL == "zsh":
            prefix = '#!/bin/zsh'
        elif SHELL == "bash":
            prefix = '#!/bin/bash'
        elif SHELL == "powershell":
            prefix = '<# powershell #>'
        elif SHELL == "unknown":
            print("\n#\tUnsupported shell type, please use # set shell <shell>")
        else:
            prefix = '#' + SHELL

        codex_query = '\n\n'.join((prefix, user_query))

        client = Groq(api_key=GROQ_API_KEY)

        # get the response from codex
        # response = openai.Completion.create(engine=config['engine'], prompt=codex_query, temperature=config['temperature'], max_tokens=config['max_tokens'], stop="#")
        response = client.chat.completions.create(messages=[
            {
                "role": "system",
                "content": "You are a handy cli completion tool. Respond only with the completion of the shell script."
            },
            {
                "role": "user",
                "content": codex_query
            }
        ], model='llama3-70b-8192')

        completion_all = response.choices[0].message.content

        print(completion_all + '\n\n')

        # if is_sensitive_content(user_query + '\n' + completion_all):
        #     print("\n#   Sensitive content detected, response has been redacted")
        # else:
        #     print(completion_all)

        #     # append output to prompt context file
        #     if config['multi_turn'] == "on":
        #         if completion_all != "" or len(completion_all) > 0:
        #             prompt_file.add_input_output_pair(user_query, completion_all)
        
    except FileNotFoundError:
        print('\n\n# Codex CLI error: Prompt file not found, try again')
    except openai.RateLimitError:
        print('\n\n# Codex CLI error: Rate limit exceeded, try later')
    except openai.APIConnectionError:
        print('\n\n# Codex CLI error: API connection error, are you connected to the internet?')
    # except openai.error.InvalidRequestError as e:
        # print('\n\n# Codex CLI error: Invalid request - ' + str(e))
    except Exception as e:
        print('\n\n# Codex CLI error: Unexpected exception - ' + str(e))

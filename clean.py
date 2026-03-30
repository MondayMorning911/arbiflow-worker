import sys

with open('bot.py', 'r') as f:
    lines = f.readlines()

with open('bot.py', 'w') as f:
    for i, line in enumerate(lines):
        if 88 <= i <= 474:
            continue
        f.write(line)

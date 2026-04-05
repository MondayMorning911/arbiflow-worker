class BotPool:
    def __init__(self, bots):
        self.bots = bots
        self.current_index = 0

    def get_next_bot(self):
        if not self.bots:
            raise Exception("No bots available in the pool.")
        bot = self.bots[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.bots)
        return bot

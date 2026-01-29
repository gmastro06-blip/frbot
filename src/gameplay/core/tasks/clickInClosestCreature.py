import src.utils.keyboard as keyboard
import src.utils.mouse as mouse
from ...typings import Context
from .common.base import BaseTask

class ClickInClosestCreatureTask(BaseTask):
    def __init__(self):
        super().__init__()
        self.name = 'clickInClosestCreature'
        self.delayOfTimeout = 1

    def shouldIgnore(self, context: Context) -> bool:
        return context.get('ng_cave', {}).get('targetCreature') is not None

    def did(self, context: Context) -> bool:
        return bool(context.get('ng_cave', {}).get('isAttackingSomeCreature', False))

    def do(self, context: Context) -> Context:
        isAttacking = bool(context.get('ng_cave', {}).get('isAttackingSomeCreature', False))
        if isAttacking == False:
            # TODO: find another way (maybe click in battle)
            # attack by mouse click when there are players on screen or ignorable creatures
            hasPlayers = len(context.get('gameWindow', {}).get('players', [])) > 0
            hasIgnorableCreatures = bool(context.get('ng_targeting', {}).get('hasIgnorableCreatures', False))
            if hasPlayers or hasIgnorableCreatures:
                closestCreature = context.get('ng_cave', {}).get('closestCreature')
                windowCoordinate = None if closestCreature is None else closestCreature.get('windowCoordinate')
                if windowCoordinate is not None:
                    keyboard.keyDown('alt')
                    mouse.leftClick(windowCoordinate)
                    keyboard.keyUp('alt')
                return context
            keyboard.press('space')

        return context
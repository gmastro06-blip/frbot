from src.gameplay.typings import Context
# import src.gameplay.utils as gameplayUtils
import src.repositories.gameWindow.slot as gameWindowSlot
from src.repositories.gameWindow.typings import Creature
import src.utils.keyboard as utilsKeyboard
from ...typings import Context
from .common.base import BaseTask


# TODO: check if something was looted or exactly count was looted
class CollectDeadCorpseTask(BaseTask):
    def __init__(self, creature: Creature):
        super().__init__()
        self.name = 'collectDeadCorpse'
        self.delayBeforeStart = 0.85
        self.creature = creature

    def do(self, context: Context) -> Context:
        utilsKeyboard.keyDown('shift')
        gameWindowSlot.rightClickSlot(
            [6, 4], context['gameWindow']['coordinate'])
        gameWindowSlot.rightClickSlot(
            [7, 4], context['gameWindow']['coordinate'])
        gameWindowSlot.rightClickSlot(
            [8, 4], context['gameWindow']['coordinate'])
        gameWindowSlot.rightClickSlot(
            [6, 5], context['gameWindow']['coordinate'])
        gameWindowSlot.rightClickSlot(
            [7, 5], context['gameWindow']['coordinate'])
        gameWindowSlot.rightClickSlot(
            [8, 5], context['gameWindow']['coordinate'])
        gameWindowSlot.rightClickSlot(
            [6, 6], context['gameWindow']['coordinate'])
        gameWindowSlot.rightClickSlot(
            [7, 6], context['gameWindow']['coordinate'])
        gameWindowSlot.rightClickSlot(
            [8, 6], context['gameWindow']['coordinate'])
        utilsKeyboard.keyUp('shift')
        return context

    def onComplete(self, context: Context) -> Context:
        radarCoordinate = context.get('ng_radar', {}).get('coordinate')
        corpsesToLoot = context.get('loot', {}).get('corpsesToLoot', [])
        if radarCoordinate is None or len(corpsesToLoot) == 0:
            return context

        x, y, z = radarCoordinate
        removed = {
            (x - 1, y - 1, z), (x, y - 1, z), (x + 1, y - 1, z),
            (x - 1, y, z),     (x, y, z),     (x + 1, y, z),
            (x - 1, y + 1, z), (x, y + 1, z), (x + 1, y + 1, z),
        }

        context['loot']['corpsesToLoot'] = [
            corpse for corpse in corpsesToLoot
            if tuple(corpse.get('coordinate')) not in removed
        ]

        return context

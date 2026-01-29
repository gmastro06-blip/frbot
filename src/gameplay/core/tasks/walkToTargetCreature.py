import numpy as np
try:
    from scipy.spatial import distance
except Exception:  # pragma: no cover
    distance = None
from src.gameplay.typings import Context
import src.gameplay.utils as gameplayUtils
from ...typings import Context
from ...utils import releaseKeys
from ..waypoint import generateFloorWalkpoints
from .common.vector import VectorTask
from .walk import WalkTask
import src.utils.keyboard as keyboard

class WalkToTargetCreatureTask(VectorTask):
    def __init__(self):
        super().__init__()
        self.name = 'walkToTargetCreature'
        self.manuallyTerminable = True
        self.targetCreatureCoordinateSinceLastRestart = None
        self.runTimesWithoutCloseMonster = 0

    def onBeforeStart(self, context: Context) -> Context:
        self.runTimesWithoutCloseMonster = self.runTimesWithoutCloseMonster + 1
        self.calculatePathToTargetCreature(context)
        return context

    def onBeforeRestart(self, context: Context) -> Context:
        context = releaseKeys(context)
        return self.onBeforeStart(context)

    def onInterrupt(self, context: Context) -> Context:
        return releaseKeys(context)

    def onComplete(self, context: Context) -> Context:
        return releaseKeys(context)

    # TODO: if there are no more creatures, it should only recalculate when it gets close to the creature to avoid recalculating each SQM move
    def shouldRestart(self, context: Context) -> bool:
        if self.runTimesWithoutCloseMonster >= 70:
            keyboard.press('esc')
            return False
        if len(self.tasks) == 0:
            keyboard.press('esc')
            return False
        if context['ng_cave']['targetCreature'] is None:
            keyboard.press('esc')
            return False
        return not gameplayUtils.coordinatesAreEqual(context['ng_cave']['targetCreature']['coordinate'], self.targetCreatureCoordinateSinceLastRestart)

    def shouldManuallyComplete(self, context: Context) -> bool:
        if context['ng_cave']['isAttackingSomeCreature'] == False:
            return True
        return False

    def calculatePathToTargetCreature(self, context: Context):
        self.tasks = []
        if context['ng_cave']['targetCreature'] is None:
            return
        nonWalkableCoordinates = context['ng_cave']['holesOrStairs'].copy()
        # TODO: also, detect players
        for monster in context['gameWindow']['monsters']:
            if np.array_equal(monster['coordinate'], context['ng_cave']['targetCreature']['coordinate']) == False:
                nonWalkableCoordinates.append(monster['coordinate'])
        walkpoints = []
        if distance is not None:
            dist = distance.cdist([context['ng_radar']['coordinate']], [
                                context['ng_cave']['targetCreature']['coordinate']]).flatten()[0]
        else:
            origin = np.asarray(context['ng_radar']['coordinate'], dtype=np.float32)
            target = np.asarray(context['ng_cave']['targetCreature']['coordinate'], dtype=np.float32)
            dist = float(np.linalg.norm(origin - target))
        if dist < 2:
            gameWindowHeight, gameWindowWidth = context['gameWindow']['image'].shape
            gameWindowCenter = (gameWindowWidth // 2, gameWindowHeight // 2)
            monsterGameWindowCoordinate = context['ng_cave']['targetCreature']['gameWindowCoordinate']
            moduleX = abs(gameWindowCenter[0] - monsterGameWindowCoordinate[0])
            moduleY = abs(gameWindowCenter[1] - monsterGameWindowCoordinate[1])
            if moduleX > 64 or moduleY > 64:
                walkpoints.append(context['ng_cave']
                                ['targetCreature']['coordinate'])
        else:
            walkpoints = generateFloorWalkpoints(
                context['ng_radar']['coordinate'], context['ng_cave']['targetCreature']['coordinate'], nonWalkableCoordinates=nonWalkableCoordinates)
            if walkpoints:
                walkpoints.pop()
        for walkpoint in walkpoints:
            self.tasks.append(WalkTask(context, walkpoint).setParentTask(
                self).setRootTask(self.rootTask))
        self.targetCreatureCoordinateSinceLastRestart = context['ng_cave']['targetCreature']['coordinate'].copy(
        )

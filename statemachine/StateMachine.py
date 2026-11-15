import threading
from collections import deque
from contextlib import contextmanager
from utils.SuppressedExceptions import noteSuppressed   # logs exceptions that are caught and ignored
from enum import IntEnum
from typing import Dict, Union
from Configuration import CosThetaConfigurator

class MachineState(IntEnum):

    INVALID_STATE = -1
    # Machine 2 sequence (28 states). Machine 1's READ_FREE_ROTATIONS_DONE (M1 state 13) is removed:
    # on Machine 2 the free rotation happens during tightening at station 2. M1 states 14-29 are now 13-28.
    READ_QR_CODE = 1
    WRITE_QR_CODE = 2
    READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE = 3
    WRITE_RESULT_OF_CHECKING_KNUCKLE = 4
    READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING = 5
    WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING = 6
    READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING = 7
    WRITE_RESULT_OF_CHECKING_TOP_BEARING = 8
    READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER = 9
    WRITE_RESULT_OF_CHECKING_NUT_AND_PLATEWASHER = 10
    READ_TIGHTENING_TORQUE_1_DONE = 11
    READ_TIGHTENING_TORQUE_1 = 12
    READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS = 13
    WRITE_RESULT_OF_CHECKING_BUNK_FOR_COMPONENT_PRESS = 14
    READ_COMPONENT_PRESS_DONE = 15
    READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK = 16
    WRITE_RESULT_OF_CHECKING_NO_BUNK = 17
    READ_TIGHTENING_TORQUE_2_DONE = 18
    READ_TIGHTENING_TORQUE_2 = 19
    READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER = 20
    WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER = 21
    READ_TAKE_PICTURE_FOR_CHECKING_CAP = 22
    WRITE_RESULT_OF_CHECKING_CAP = 23
    READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS = 24
    WRITE_RESULT_OF_CHECKING_BUNK_FOR_CAP_PRESS = 25
    READ_CAP_PRESS_DONE = 26
    READ_FREE_ROTATION_TORQUE_1_DONE = 27
    READ_FREE_ROTATION_TORQUE_1 = 28

    @classmethod
    def getMachineStateFromString(cls, state_str: Union[str,'MachineState']) -> 'MachineState':
        """
        Convert a string representation of a MachineState to the corresponding enum member.

        Args:
            state_str: String representation, e.g., '<MachineState.READ_QR_CODE: 1>' or 'READ_QR_CODE'.

        Returns:
            MachineState: The corresponding enum member, or MachineState.INVALID_STATE if conversion fails.
        """
        # Try parsing based on integer value
        if isinstance(state_str, MachineState):
            return state_str
        try:
            # Extract integer from string like '<MachineState.READ_QR_CODE: 1>'
            state_value = int(state_str.split(':')[-1].strip('>'))
            return cls(state_value)
        except (ValueError, IndexError, KeyError):
            noteSuppressed()  # Proceed to name-based parsing

        # Try parsing based on enum name
        try:
            # Extract name from string like '<MachineState.READ_QR_CODE: 1>' or use directly if it's just 'READ_QR_CODE'
            if '.' in state_str:
                state_name = state_str.split('.')[1].split(':')[0]
            else:
                state_name = state_str.strip()
            return cls.__members__[state_name]
        except (KeyError, IndexError):
            noteSuppressed()  # Return INVALID_STATE

        return cls.INVALID_STATE

    @classmethod
    def getMachineStateAsInt(cls, givenState: Union[str,'MachineState']) -> int:
        """
        Return the integer value of a given MachineState enum member.

        Args:
            givenState: A MachineState enum member.

        Returns:
            int: The integer value of the given MachineState.
        """
        if isinstance(givenState, str):
            return cls.getMachineStateAsInt(cls.getMachineStateFromString(givenState))

        return givenState.value

# Previously declared inside MachineState, where IntEnum silently turned it into an alias of state 5.
MAX_SECONDS_TO_WAIT_FOR_CAMERA_FEEDBACK: float = CosThetaConfigurator.getInstance().getMaxSecondsToWaitForCameraFeedback()


class MachineStateMachine:
    def __init__(self):
        self._lock = threading.RLock()        # state changes from several IOServer threads (loops, abort)
        self._currentState: MachineState = MachineState.READ_QR_CODE
        self.stateChangeListener = None      # listener(oldState, newState), e.g. the IOServer's audit trail
        # The listener is called after the state lock is released (CHANGES 110): before, it ran under the lock, and
        # the IOServer's listener (state publish, end of cycle to the camera: Redis calls with 1 s timeouts) held up
        # every other thread changing the state. Changes are queued in order under the lock and delivered by one
        # thread at a time, in that order.
        self._pendingChanges: deque = deque()
        self._deliveryLock = threading.Lock()
        self._depth = threading.local()      # this thread's nesting of state-changing calls
        self._reasonForNextChange = None      # given with setCurrentState(..., reason=); queued with that change
        # Instruction text comes from properties key state.<N>.instruction, N = the state's integer value
        self._instructions: Dict[MachineState, str] = {
            aState: CosThetaConfigurator.getInstance().getInstructionForState(stateNumber=aState.value)
            for aState in MachineState if aState != MachineState.INVALID_STATE
        }
        CosThetaConfigurator.addReloadListener(self._refreshInstructions)

    @property
    def currentState(self) -> MachineState:
        return self._currentState

    @currentState.setter
    def currentState(self, newState: MachineState) -> None:
        with self._changing():
            old, self._currentState = getattr(self, "_currentState", None), newState
            reason, self._reasonForNextChange = self._reasonForNextChange, None
            if old != newState:
                self._pendingChanges.append((old, newState, reason))

    @contextmanager
    def _changing(self):
        """The state lock for a state-changing call; when the outermost one of this thread ends, the queued changes are
        delivered to the listener outside the lock."""
        self._lock.acquire()
        self._depth.value = getattr(self._depth, "value", 0) + 1
        try:
            yield
        finally:
            self._depth.value -= 1
            outermost = self._depth.value == 0
            self._lock.release()
            if outermost:
                self._deliverChanges()

    def _deliverChanges(self) -> None:
        """Calls the listener for each queued change, in order, outside the state lock. One thread delivers at a time;
        a thread that finds another delivering returns at once (that thread delivers its change too)."""
        while True:
            if not self._deliveryLock.acquire(blocking=False):
                return
            try:
                while True:
                    with self._lock:
                        if not self._pendingChanges:
                            break
                        old, new, reason = self._pendingChanges.popleft()
                    listener = getattr(self, "stateChangeListener", None)
                    if listener is not None:
                        try:
                            # the reason travels with its own change (CHANGES 112); listeners without one get two args
                            listener(old, new) if reason is None else listener(old, new, reason)
                        except Exception:
                            noteSuppressed()
            finally:
                self._deliveryLock.release()
            with self._lock:
                if not self._pendingChanges:
                    return

    def _refreshInstructions(self, changedKeys=None) -> None:
        """Instruction texts are re-read when the properties file changes (state.<N>.instruction)."""
        if changedKeys is not None and not any(k.startswith("state.") for k in changedKeys):
            return
        self._instructions = {
            aState: CosThetaConfigurator.getInstance().getInstructionForState(stateNumber=aState.value)
            for aState in MachineState if aState != MachineState.INVALID_STATE
        }

    def incrementState(self, stateJumps : int = 1) -> None:
        # """Increment the current state, wrapping around to READ_QR_CODE if at the end."""
        # if self.currentState == MachineState.READ_QR_CODE:
        #     self.currentState = MachineState(self.currentState + 2)
        # else:
        #     self.currentState = MachineState(self.currentState + 1)
        with self._changing():
            newState = self.currentState + stateJumps
            if newState > MachineState.READ_FREE_ROTATION_TORQUE_1:
                self.currentState = MachineState.READ_QR_CODE
            else:
                self.currentState = MachineState(self.currentState + stateJumps)

    def decrementState(self, stateJumps : int = 1) -> None:
        # """Increment the current state, wrapping around to READ_QR_CODE if at the end."""
        # if self.currentState == MachineState.READ_QR_CODE:
        #     self.currentState = MachineState(self.currentState + 2)
        # else:
        #     self.currentState = MachineState(self.currentState + 1)
        with self._changing():
            newState = self.currentState - stateJumps
            if newState < MachineState.READ_QR_CODE:
                self.currentState = MachineState.READ_QR_CODE
            else:
                self.currentState = MachineState(self.currentState - stateJumps)

    def incrementStateFrom(self, expectedState: MachineState, stateJumps: int = 1) -> bool:
        """Increment only if the state is still expectedState (atomically). False if another thread (an abort) has
        changed the state meanwhile: a step finishing after an abort must not move the reset cycle on."""
        with self._changing():
            if self._currentState != expectedState:
                return False
            self.incrementState(stateJumps)
            return True

    def decrementStateFrom(self, expectedState: MachineState, stateJumps: int = 1) -> bool:
        """Decrement only if the state is still expectedState (atomically); see incrementStateFrom."""
        with self._changing():
            if self._currentState != expectedState:
                return False
            self.decrementState(stateJumps)
            return True

    def getCurrentState(self) -> MachineState:
        """Return the current state."""
        return self.currentState

    def setCurrentState(self, newState: Union[int, MachineState], reason: Union[str, None] = None) -> None:
        """Set the current state, ensuring it's valid. reason: passed to the listener with this change only."""
        with self._changing():
            self._reasonForNextChange = reason
            if isinstance(newState, MachineState):
                if not (MachineState.READ_QR_CODE <= newState <= MachineState.READ_FREE_ROTATION_TORQUE_1):
                    newState = MachineState.READ_QR_CODE
                self.currentState = newState
            else:
                if not (MachineState.READ_QR_CODE <= newState <= MachineState.READ_FREE_ROTATION_TORQUE_1):
                    newState = MachineState.READ_QR_CODE
                self.currentState = MachineState(newState)

    def goToFirstState(self) -> None:
        """Set the state to READ_QR_CODE."""
        self.setCurrentState(MachineState.READ_QR_CODE)

    def goToLastState(self) -> None:
        """Set the state to READ_FREE_ROTATION_TORQUE_1."""
        self.setCurrentState(MachineState.READ_FREE_ROTATION_TORQUE_1)

    def getCurrentInstruction(self) -> str:
        """Return the instruction for the current state."""
        return self._instructions.get(self.currentState, "No available Instruction")

    def getLongestInstruction(self) -> str:
        longestInstruction : str = ""
        for key in self._instructions:
            value : str = str(self._instructions[key])
            if len(value) > len(longestInstruction):
                longestInstruction = value
        return longestInstruction
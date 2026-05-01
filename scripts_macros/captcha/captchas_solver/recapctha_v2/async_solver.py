from __future__ import annotations

import asyncio
import base64
import functools
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
from json import JSONDecodeError
from typing import Any, BinaryIO, Dict, List, Optional, Union, Iterable
from urllib.parse import parse_qs, urlparse

import speech_recognition
from playwright.async_api import Locator, Page, Response
from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_delay,
    wait_fixed,
)

from ..errors import (
    RecaptchaNotFoundError,
    RecaptchaRateLimitError,
    RecaptchaSolveError,
)
from .base_solver import BaseSolver
from .recaptcha_box import AsyncRecaptchaBox
from .translations import OBJECT_TRANSLATIONS, ORIGINAL_LANGUAGE_AUDIO


class AsyncAudioFile(speech_recognition.AudioFile):
    """
    A subclass of `speech_recognition.AudioFile` that can be used asynchronously.

    Parameters
    ----------
    file : Union[BinaryIO, str]
        The audio file handle or file path.
    executor : Optional[ThreadPoolExecutor], optional
        The thread pool executor to use, by default None.
    """

    def __init__(
        self,
        file: Union[BinaryIO, str],
        *,
        executor: Optional[ThreadPoolExecutor] = None,
    ) -> None:
        super().__init__(file)
        self._loop = asyncio.get_event_loop()
        self._executor = executor

    async def __aenter__(self) -> AsyncAudioFile:
        await self._loop.run_in_executor(self._executor, self.__enter__)
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._loop.run_in_executor(self._executor, self.__exit__, *args)


class AsyncSolver(BaseSolver[Page]):
    """
    A class for solving reCAPTCHA v2 asynchronously with Playwright.

    Parameters
    ----------
    page : Page
        The Playwright page to solve the reCAPTCHA on.
    attempts : int, optional
        The number of solve attempts, by default 5.
    capsolver_api_key : Optional[str], optional
        The CapSolver API key, by default None.
        If None, the `CAPSOLVER_API_KEY` environment variable will be used.
    """

    def __init__(
        self, page: Page, *, attempts: int = 5, capsolver_api_key: Optional[str] = None
    ) -> None:
        super().__init__(page, attempts=attempts, capsolver_api_key=capsolver_api_key)
        self._token_event = asyncio.Event()

    async def __aenter__(self) -> AsyncSolver:
        return self

    async def __aexit__(self, *_: Any) -> None:
        self.close()

    @staticmethod
    def _get_task_object(recaptcha_box: AsyncRecaptchaBox) -> Optional[str]:
        return None

    async def _response_callback(self, response: Response) -> None:
        if (
            re.search("/recaptcha/(api2|enterprise)/payload", response.url) is not None
            and self._payload_response is None
        ):
            self._payload_response = response
        elif (
            re.search("/recaptcha/(api2|enterprise)/userverify", response.url)
            is not None
        ):
            text = await response.text()
            token_match = re.search('"uvresp","(.*?)"', text)

            if token_match is not None:
                self._token = token_match.group(1)
                self._token_event.set()

    def _get_capsolver_response(
        self, recaptcha_box: AsyncRecaptchaBox, image_data: bytes
    ) -> Optional[Dict[str, Any]]:
        return None

    def _solve_tiles(self, recaptcha_box: AsyncRecaptchaBox, indexes: Iterable[int]) -> None:
        pass

    def _submit_tile_answers(self, recaptcha_box: AsyncRecaptchaBox) -> None:
        pass

    async def _solve_image_challenge(self, recaptcha_box: AsyncRecaptchaBox) -> None:
        pass


    async def _transcribe_audio(
        self, audio_url: str, *, language: str = "en-US"
    ) -> Optional[str]:
        """
        Transcribe the reCAPTCHA audio challenge.

        Parameters
        ----------
        audio_url : str
            The reCAPTCHA audio URL.
        language : str, optional
            The language of the audio, by default en-US.

        Returns
        -------
        Optional[str]
            The reCAPTCHA audio text.
            Returns None if the audio could not be converted.
        """
        loop = asyncio.get_event_loop()
        response = await self._page.request.get(audio_url)

        wav_audio = BytesIO()
        mp3_audio = BytesIO(await response.body())

        try:
            audio: AudioSegment = await loop.run_in_executor(
                None, AudioSegment.from_mp3, mp3_audio
            )
        except CouldntDecodeError:
            return None

        await loop.run_in_executor(
            None, functools.partial(audio.export, wav_audio, format="wav")
        )

        recognizer = speech_recognition.Recognizer()

        async with AsyncAudioFile(wav_audio) as source:
            audio_data = await loop.run_in_executor(None, recognizer.record, source)

        try:
            return await loop.run_in_executor(
                None,
                functools.partial(
                    recognizer.recognize_google, audio_data, language=language
                ),
            )
        except speech_recognition.UnknownValueError:
            return None

    async def _click_checkbox(self, recaptcha_box: AsyncRecaptchaBox) -> None:
        """
        Click the reCAPTCHA checkbox.

        Parameters
        ----------
        recaptcha_box : AsyncRecaptchaBox
            The reCAPTCHA box.

        Raises
        ------
        RecaptchaRateLimitError
            If the reCAPTCHA rate limit has been exceeded.
        """
        await recaptcha_box.checkbox.click()

        while recaptcha_box.frames_are_attached() and self._token is None:
            if await recaptcha_box.rate_limit_is_visible():
                raise RecaptchaRateLimitError

            if await recaptcha_box.any_challenge_is_visible():
                return

            await self._page.wait_for_timeout(250)

    async def _get_audio_url(self, recaptcha_box: AsyncRecaptchaBox) -> str:
        """
        Get the reCAPTCHA audio URL.

        Parameters
        ----------
        recaptcha_box : AsyncRecaptchaBox
            The reCAPTCHA box.

        Returns
        -------
        str
            The reCAPTCHA audio URL.

        Raises
        ------
        RecaptchaRateLimitError
            If the reCAPTCHA rate limit has been exceeded.
        """
        while True:
            if await recaptcha_box.rate_limit_is_visible():
                raise RecaptchaRateLimitError

            if await recaptcha_box.audio_challenge_is_visible():
                return await recaptcha_box.audio_download_button.get_attribute("href")

            await self._page.wait_for_timeout(250)

    async def _submit_audio_text(
        self, recaptcha_box: AsyncRecaptchaBox, text: str
    ) -> None:
        """
        Submit the reCAPTCHA audio text.

        Parameters
        ----------
        recaptcha_box : AsyncRecaptchaBox
            The reCAPTCHA box.
        text : str
            The reCAPTCHA audio text.

        Raises
        ------
        RecaptchaRateLimitError
            If the reCAPTCHA rate limit has been exceeded.
        """
        await recaptcha_box.audio_challenge_textbox.fill(text)

        async with self._page.expect_response(
            re.compile("/recaptcha/(api2|enterprise)/userverify")
        ) as response:
            await recaptcha_box.verify_button.click()

        await response.value

        while recaptcha_box.frames_are_attached():
            if await recaptcha_box.rate_limit_is_visible():
                raise RecaptchaRateLimitError

            if (
                not await recaptcha_box.audio_challenge_is_visible()
                or await recaptcha_box.solve_failure_is_visible()
                or await recaptcha_box.challenge_is_solved()
            ):
                return

            await self._page.wait_for_timeout(250)


    async def _solve_audio_challenge(self, recaptcha_box: AsyncRecaptchaBox) -> None:
        """
        Solve the reCAPTCHA audio challenge.

        Parameters
        ----------
        recaptcha_box : AsyncRecaptchaBox
            The reCAPTCHA box.

        Raises
        ------
        RecaptchaRateLimitError
            If the reCAPTCHA rate limit has been exceeded.
        """
        parsed_url = urlparse(recaptcha_box.anchor_frame.url)
        query_params = parse_qs(parsed_url.query)
        language = query_params["hl"][0]

        if language not in ORIGINAL_LANGUAGE_AUDIO:
            language = "en-US"

        while True:
            url = await self._get_audio_url(recaptcha_box)
            text = await self._transcribe_audio(url, language=language)

            if text is not None:
                break

            async with self._page.expect_response(
                re.compile("/recaptcha/(api2|enterprise)/reload")
            ) as response:
                await recaptcha_box.new_challenge_button.click()

            await response.value

            while url == await self._get_audio_url(recaptcha_box):
                await self._page.wait_for_timeout(250)

        await self._submit_audio_text(recaptcha_box, text)

    async def recaptcha_is_visible(self) -> bool:
        """
        Check if a reCAPTCHA challenge or unchecked reCAPTCHA box is visible.

        Returns
        -------
        bool
            Whether a reCAPTCHA challenge or unchecked reCAPTCHA box is visible.
        """
        try:
            await AsyncRecaptchaBox.from_frames(self._page.frames)
        except RecaptchaNotFoundError:
            return False

        return True

    async def solve_recaptcha(
        self,
        *,
        attempts: Optional[int] = None,
        wait: bool = False,
        wait_timeout: float = 30,
        image_challenge: bool = False,
    ) -> str:
        """
        Solve the reCAPTCHA and return the `g-recaptcha-response` token.

        Parameters
        ----------
        attempts : Optional[int], optional
            The number of solve attempts, by default 5.
        wait : bool, optional
            Whether to wait for the reCAPTCHA to appear, by default False.
        wait_timeout : float, optional
            The amount of time in seconds to wait for the reCAPTCHA to appear,
            by default 30. Only used if `wait` is True.
        image_challenge : bool, optional
            Whether to solve the image challenge, by default False.

        Returns
        -------
        str
            The `g-recaptcha-response` token.

        Raises
        ------
        RecaptchaNotFoundError
            If the reCAPTCHA was not found.
        RecaptchaRateLimitError
            If the reCAPTCHA rate limit has been exceeded.
        RecaptchaSolveError
            If the reCAPTCHA could not be solved.
        """
        self._token = None
        self._token_event.clear()
        attempts = attempts or self._attempts

        if wait:
            retry = AsyncRetrying(
                sleep=self._page.wait_for_timeout,
                stop=stop_after_delay(wait_timeout),
                wait=wait_fixed(0.25),
                retry=retry_if_exception_type(RecaptchaNotFoundError),
                reraise=True,
            )

            recaptcha_box = await retry(
                lambda: AsyncRecaptchaBox.from_frames(self._page.frames)
            )
        else:
            recaptcha_box = await AsyncRecaptchaBox.from_frames(self._page.frames)

        if await recaptcha_box.rate_limit_is_visible():
            raise RecaptchaRateLimitError

        if await recaptcha_box.checkbox.is_visible():
            await self._click_checkbox(recaptcha_box)

            if self._token is not None:
                return self._token

            if (
                recaptcha_box.frames_are_detached()
                or not await recaptcha_box.any_challenge_is_visible()
                or await recaptcha_box.challenge_is_solved()
            ):
                await self._token_event.wait()
                return self._token

        while not await recaptcha_box.any_challenge_is_visible():
            await self._page.wait_for_timeout(250)

        if await recaptcha_box.audio_challenge_button.is_visible():
            await recaptcha_box.audio_challenge_button.click()

        while attempts > 0:
            self._token = None
            self._token_event.clear()
            await self._solve_audio_challenge(recaptcha_box)

            if (
                recaptcha_box.frames_are_detached()
                or not await recaptcha_box.any_challenge_is_visible()
                or await recaptcha_box.challenge_is_solved()
            ):
                await self._token_event.wait()
                return self._token

            attempts -= 1

        raise RecaptchaSolveError

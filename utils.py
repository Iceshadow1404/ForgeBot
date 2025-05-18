from logs import logger


def format_time_difference(milliseconds: float) -> str:
    """
    Formats a time difference in milliseconds into a human-readable string.
    Ignores seconds if duration is 1 hour or more.
    """
    # logger.debug(f"Entering format_time_difference with milliseconds: {milliseconds}")
    if milliseconds <= 0:
        # logger.debug("format_time_difference returning 'Finished'")
        return "Finished"

    total_seconds = int(milliseconds // 1000)

    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    parts = []
    if days > 0:
        parts.append(f"{days}d")

    if milliseconds >= 3_600_000:
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
    else:
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if seconds > 0 or not parts:
            parts.append(f"{seconds}s")

    if not parts and milliseconds > 0:
        # logger.debug("format_time_difference returning '<1s'")
        return "<1s"
    elif not parts and milliseconds <= 0:
        # logger.debug("format_time_difference returning 'Finished'")
        return "Finished"

    # logger.debug(f"format_time_difference returning: {' '.join(parts)}")
    return " ".join(parts)

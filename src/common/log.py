"""Logger factory for the Lambdas.

The Lambda runtime attaches a CloudWatch handler but leaves the root level at WARNING, which
would drop the INFO-level request lines and delivery receipts; raising each module logger to
INFO here keeps that decision in one place.

This is only the first of two gates: the runtime filters again at the ApplicationLogLevel set
in scripts/template.yaml, and the stricter level wins. INFO here means "emit everything the
deployment chooses to show", with the actual verbosity governed by the template.
"""

import logging


def get_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    return logger

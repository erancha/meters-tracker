"""The owner's story: the names, labels, serials, readings and prices that
tests/test_owner_story.py, scripts/replay.py and scripts/eval_intents.py share. The readings,
the water meter and the prices are the run verified in the browser on 29/10/2026; the
electricity meter goes by the label written beside it, as every electricity meter does."""

from datetime import date

TODAY = date(2026, 10, 29)
PROPERTY = "לאונר 18"
APARTMENT = "דירה קדמית"
# The apartment as the owner types it in a request, and as written by hand beside the
# electricity meter, whose serial it becomes; the model misreads it on the first photo as
# LABEL_READ, and the owner corrects it.
LABEL = "קדמית"
LABEL_READ = "קדמת"
# The answer to the property question that names the apartment too.
PLACE = f"{PROPERTY}, {APARTMENT}"
# A printed serial the model misreads and the one the owner corrects it to, in the card and
# record contexts scripts/eval_intents.py builds for the classifier.
SERIAL_READ = "2721056"
SERIAL = "2721058"
WATER_SERIAL = "4410273"
# The electricity readings that open and close the first period, and the water meter's opening.
OPENING = 37937
CLOSING = 39269
WATER_OPENING = 1251
# A two-number display shows the lower number this much above the upper one.
BOTTOM_OFFSET = 3
PERIOD_START = date(2026, 9, 1)
NEXT_START = date(2026, 10, 30)
# A later day of the second period, past the re-close window of the 29/10 closing readings.
LATER = date(2026, 11, 15)
PRICES = {"electricity": 0.6432, "water": 15.62}
TYPED_PRICES = f"חשמל {PRICES['electricity']}, מים {PRICES['water']}"
CANCEL = "בטל"

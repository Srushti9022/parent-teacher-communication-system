"""Grade scale. It was worked out from the existing marks documents
(A+ = 90-100, A = 80-89, B+ = 70-79, B = 60-69, C = 50-59, D = 40-49, F = below 40)."""

SCALE = [(90, "A+"), (80, "A"), (70, "B+"), (60, "B"), (50, "C"), (40, "D")]
EXAM_ORDER = ["Unit Test 1", "Mid-Term"]   # display order only


def grade_for(percentage):
    for minimum, letter in SCALE:
        if percentage >= minimum:
            return letter
    return "F"


def percentage_for(obtained, maximum):
    return round(obtained * 100 / maximum, 1)


def exam_sort_key(name):
    return (EXAM_ORDER.index(name) if name in EXAM_ORDER else 99, name)

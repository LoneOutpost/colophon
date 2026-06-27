"""Curated real folders from TE_Audiobooks_S for clustering regression.

Each: (label, filenames, expected_kind, expected_work_count).
"""
from colophon.core.models import ContentKind

S = ContentKind.SINGLE
M = ContentKind.MULTI

CORPUS = [
    ("Srini Pillay - single", ["Tinker Dabble Doodle.mp3"], S, 1),
    ("Sammy Hagar - single", ["Red, My Uncensored Life in Rock.mp3"], S, 1),
    ("7th Sigma - two parts", [
        "7thSigmaUnabridgedPart1_ep6.mp3", "7thSigmaUnabridgedPart2_ep6.mp3"], S, 1),
    ("girlblue - cd tracks", [
        "girlblue-01-cd01-01.mp3", "girlblue-02-cd01-02.mp3", "girlblue-03-cd01-03.mp3",
        "girlblue-07-cd02-01.mp3", "girlblue-12-cd03-01.mp3", "girlblue-20-cd04-01.mp3"], S, 1),
    ("Sarah Noffke - series, mixed comma", [
        "Alpha Wolf (Olento Research, 1).mp3", "Bad Wolf (Olento Research 4).mp3"], M, 2),
    ("Stanley Weintraub - two standalones", [
        "Pearl Harbor Christmas.mp3", "eleven Days in December.mp3"], M, 2),
    ("S E England - loose books, ragged", [
        "Father of Lies (Darkly Disturbing Trilogy 1).mp3", "Hidden Company.mp3",
        "Magda (Darkly Disturbing Trilogy 3).mp3", "Owlmen.mp3", "Soprano.mp3",
        "Tanners Dell (Darkly Disturbing Trilogy 2).mp3"], M, 6),
    ("Sarah Graves - two series", [
        "A Face at the Window (Home Repair is Homicide 12).mp3",
        "Dead Cat Bounce (Home Repair is Homicide 1).mp3",
        "Death by Chocolate Malted Milkshake (Death by Chocolate 2).mp3"], M, 3),
    ("Sally MacKenzie - single, decimal seq", [
        "Duchess of Love (Duchess of Love Trilogy 0.5).mp3"], S, 1),
]

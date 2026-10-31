package io.github.obamna1234.sevby

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import kotlinx.coroutines.runBlocking
import org.junit.Test

/**
 * Song-matching rules shared with the desktop app (wrong-version bug of October 2026):
 * strict Bandcamp band names, no remakes/tributes/dubs/live albums, no "first hit" YouTube guesses.
 * Run in Android Studio: right-click this file → Run.
 */
class MatchingRulesTest {

    private fun hit(name: String, band: String, sub: String, album: String) =
        BcHit(name, band, "https://$sub.bandcamp.com/track/${name.lowercase().replace(' ', '-')}", album)

    // ── Bandcamp: wrong versions are rejected ───────────────────────────

    @Test fun queenTributeBandRejected_realQueenKept() {
        val tribute = hit("Bohemian Rhapsody", "Queen Mary Band", "queenmaryband", "Live at Southern Tier Brewing")
        val real = hit("Bohemian Rhapsody", "Queen", "queen", "A Night at the Opera")
        assertEquals(listOf(real), BandcampData.choose(listOf(tribute, real), "Queen - Bohemian Rhapsody"))
        assertTrue(BandcampData.choose(listOf(tribute), "Queen - Bohemian Rhapsody").isEmpty())
    }

    @Test fun weekndStudioRemakeRejected() {
        val remake = hit("Blinding Lights", "The Weeknd", "afterhourstildawn", "After Hours Til Dawn (studio remake)")
        assertTrue(BandcampData.choose(listOf(remake), "The Weeknd - Blinding Lights").isEmpty())
        val real = hit("Blinding Lights", "The Weeknd", "theweeknd", "After Hours")
        assertEquals(listOf(real), BandcampData.choose(listOf(remake, real), "The Weeknd - Blinding Lights"))
    }

    @Test fun daftPunkDubsRejected() {
        val dubs = hit("Around the World", "Daft Punk", "eudubs", "EU Dubs")
        assertTrue(BandcampData.choose(listOf(dubs), "Daft Punk - Around the World").isEmpty())
    }

    @Test fun unrelatedBandRejected() {
        val other = hit("Anti-Hero", "ulker", "ulker", "pop music / HOT TOP 2023")
        assertTrue(BandcampData.choose(listOf(other), "Taylor Swift - Anti-Hero").isEmpty())
    }

    @Test fun nearbyNamesAreNotTheSameBand() {
        assertFalse(BandcampData.bandMatches("Queen", "Queen Mary Band"))
        assertFalse(BandcampData.bandMatches("Queen", "Queen Latifah"))
        assertFalse(BandcampData.bandMatches("Taylor Swift", "ulker"))
    }

    // ── Bandcamp: artist-page rule (own page first, other accounts only with the iTunes album) ──

    private fun page(name: String, band: String, url: String, album: String) = BcHit(name, band, url, album)

    @Test fun otherAccountsCreditingTheArtistAreRejected() {
        val cases = listOf(
            Triple("AC/DC - Highway to Hell",
                page("Highway to Hell", "AC/DC", "https://cherrybox.bandcamp.com/track/highway-to-hell", "Full Black (Chronicles of the Abyss) Vol. 3"),
                "Highway to Hell"),
            Triple("Taylor Swift - Anti-Hero",
                page("Anti-Hero", "Taylor Swift", "https://ulker.bandcamp.com/track/anti-hero", "pop music / HOT TOP 2023"),
                "Midnights"),
            Triple("Queen - Bohemian Rhapsody",
                page("Bohemian Rhapsody", "Queen", "https://djrocco.bandcamp.com/track/bohemian-rhapsody", "Club Edits 2024"),
                "A Night at the Opera"),
            Triple("Daft Punk - Around the World",
                page("Around the World", "Daft Punk", "https://steppersclub.bandcamp.com/track/around-the-world", "EU Dubs"),
                "Homework"),
        )
        for ((song, h, itunesAlbum) in cases) {
            assertTrue(song, BandcampData.choose(listOf(h), song, itunesAlbum).isEmpty())
            assertTrue(song, BandcampData.choose(listOf(h), song, null).isEmpty())        // no iTunes album → also rejected
        }
    }

    @Test fun ownArtistPagesAreAccepted() {
        val cases = listOf(
            "Perturbator - Future Club" to page("Future Club", "Perturbator", "https://perturbator.bandcamp.com/track/future-club", "Dangerous Days"),
            "Carpenter Brut - Turbo Killer" to page("Turbo Killer", "Carpenter Brut", "https://carpenterbrut.bandcamp.com/track/turbo-killer", "Turbo Killer"),
            "Mitski - Nobody" to page("Nobody", "Mitski", "https://mitski.bandcamp.com/track/nobody", "Be the Cowboy"),
            "Kevin MacLeod - Monkeys Spinning Monkeys" to page("Monkeys Spinning Monkeys", "Kevin MacLeod", "https://kevinmacleod.bandcamp.com/track/monkeys-spinning-monkeys", ""),
            "Broke For Free - Night Owl" to page("Night Owl", "Broke For Free", "https://brokeforfree.bandcamp.com/track/night-owl", "Directionless EP"),
            "Jahzzar - Siesta" to page("Siesta", "Jahzzar", "https://jahzzar.bandcamp.com/track/siesta", "Traveller's Guide"),
            "Sigur Ros - Hoppipolla" to page("Hoppípolla", "Sigur Rós", "https://store.sigurros.com/track/hopp-polla", "Takk..."),
        )
        for ((song, h) in cases) {
            assertEquals(song, listOf(h), BandcampData.choose(listOf(h), song, null))     // no iTunes needed for own pages
            assertFalse(song, BandcampData.needsItunesAlbum(listOf(h), song))
        }
    }

    @Test fun labelPageAcceptedOnlyWhenAlbumMatchesItunes() {
        val label = page("Asteroid Rain", "Wice", "https://newretrowave.bandcamp.com/track/asteroid-rain", "Asteroid Rain")
        assertTrue(BandcampData.needsItunesAlbum(listOf(label), "Wice - Asteroid Rain"))
        assertEquals(listOf(label), BandcampData.choose(listOf(label), "Wice - Asteroid Rain", "Asteroid Rain"))
        assertTrue(BandcampData.choose(listOf(label), "Wice - Asteroid Rain", "Magnatron 2.0").isEmpty())
        assertTrue(BandcampData.choose(listOf(label), "Wice - Asteroid Rain", null).isEmpty())
        val noAlbum = page("Asteroid Rain", "Wice", "https://newretrowave.bandcamp.com/track/asteroid-rain", "")
        assertTrue(BandcampData.choose(listOf(noAlbum), "Wice - Asteroid Rain", "Asteroid Rain").isEmpty())
    }

    @Test fun ownPageWinsOverLabelPage() {
        val label = page("Future Club", "Perturbator", "https://bloodmusic.bandcamp.com/track/future-club", "Dangerous Days")
        val own = page("Future Club", "Perturbator", "https://perturbator.bandcamp.com/track/future-club", "Dangerous Days")
        assertEquals(listOf(own), BandcampData.choose(listOf(label, own), "Perturbator - Future Club", "Dangerous Days"))
    }

    @Test fun pageAccountReadsSubdomainsAndCustomDomains() {
        assertEquals("perturbator", BandcampData.pageAccount("https://perturbator.bandcamp.com/track/future-club"))
        assertEquals("sigurros", BandcampData.pageAccount("https://store.sigurros.com/track/hopp-polla"))
        assertEquals("example", BandcampData.pageAccount("https://music.example.co.uk/track/x"))
    }

    // ── Bandcamp: real artist pages still match ─────────────────────────

    @Test fun ownPagesStillMatch() {
        val cases = listOf(
            "Carpenter Brut - Turbo Killer" to hit("Turbo Killer", "Carpenter Brut", "carpenterbrut", "Turbo Killer"),
            "Perturbator - Future Club" to hit("Future Club", "Perturbator", "perturbator", "Dangerous Days"),
            "Mitski - Nobody" to hit("Nobody", "Mitski", "mitski", "Be the Cowboy"),
            "Sigur Ros - Hoppipolla" to hit("Hoppípolla", "Sigur Rós", "sigurros", "Takk..."),
            "Kevin MacLeod - Monkeys Spinning Monkeys" to hit("Monkeys Spinning Monkeys", "Kevin MacLeod", "kevinmacleod", "Monkeys Spinning Monkeys"),
            "Jahzzar - Siesta" to hit("Siesta", "Jahzzar", "jahzzar", "Traveller's Guide"),
            "Simon & Garfunkel - The Sound of Silence" to hit("The Sound of Silence", "Simon & Garfunkel", "simongarfunkel", "Wednesday Morning, 3 A.M."),
        )
        for ((song, h) in cases) assertEquals(song, listOf(h), BandcampData.choose(listOf(h), song))
    }

    @Test fun collaborationsAndShortNamesStillMatch() {
        assertTrue(BandcampData.bandMatches("Power Glove,PYLOT", "Power Glove"))
        assertTrue(BandcampData.bandMatches("M.A.D.E.S,Wice", "M.A.D.E.S"))
        assertTrue(BandcampData.bandMatches("Simon & Garfunkel", "Simon and Garfunkel"))
        assertTrue(BandcampData.bandMatches("LXST CXNTURY,Kingpin Skinny Pimp", "LXST CXNTURY"))
    }

    @Test fun liveAndVersionAllowedWhenTheListAsksForThem() {
        assertNotNull(BandcampData.notOriginal("Kataklysm - The Black Sheep", "The Black Sheep", "Live in Deutschland"))
        assertNull(BandcampData.notOriginal("Kataklysm - The Black Sheep (Live)", "The Black Sheep (Live)", "Live in Deutschland"))
        assertNull(BandcampData.notOriginal("VHS Dreams - Vice Point - 2022 Remastered Version", "Vice Point - 2022 Remastered Version", "Vice Point"))
        assertNotNull(BandcampData.notOriginal("Artist - Song", "Song (Piano Version)", "Lullaby Renditions"))
    }

    // ── YouTube: no "first hit" guesses ─────────────────────────────────

    @Test fun madeUpSongMatchesNoVideo() {
        assertEquals(0.0, Matching.titleShare("Notarealsong", "Relaxing Rain Sounds for Sleep 10 Hours"), 0.0)
        assertTrue(Matching.titleShare("Notarealsong", "Zzzz - Fake Video") < 0.5)
    }

    @Test fun realVideosStillPassTheTitleCheck() {
        assertTrue(Matching.titleShare("Bohemian Rhapsody", "Queen – Bohemian Rhapsody (Official Video Remastered)") >= 0.5)
        assertEquals(1.0, Matching.titleShare("In Shadows and Dust", "In Shadows & Dust (Remastered)"), 0.0)
        assertTrue(Matching.titleShare(Matching.cleanTitle("I Like You (feat. DRAM & 6LACK)"), "Childish Major ft. DRAM & 6LACK - I Like You") >= 0.5)
        assertTrue(Matching.titleShare("Sweet Chid O' Mine", "Guns N' Roses - Sweet Child O' Mine (Official Music Video)") >= 0.5)
    }

    @Test fun unrelatedYouTubeResultsGiveNoCandidates() {
        // Nothing left to download means the song fails – there is no "ytsearch1" first-hit fallback any more.
        val unrelated = listOf(
            YtEntry("a", "a", "Relaxing Rain Sounds for Sleep 10 Hours", "Calm", 36000.0),
            YtEntry("b", "b", "Top 10 Funny Cat Videos", "Cats", 600.0),
            YtEntry("c", "c", "Fake Plastic Trees", "Radiohead", 290.0),
        )
        val ranked = Matching.rank(unrelated, "Zzzz Fakeartist - Notarealsong")
        assertTrue(Matching.candidates(ranked, "Zzzz Fakeartist - Notarealsong").isEmpty())

        val real = listOf(YtEntry("q", "q", "Queen – Bohemian Rhapsody (Official Video Remastered)", "Queen Official", 359.0))
        assertEquals(1, Matching.candidates(Matching.rank(real + unrelated, "Queen - Bohemian Rhapsody"), "Queen - Bohemian Rhapsody").size)
    }

    // ── Length check after a Bandcamp download ──────────────────────────

    @Test fun bandcampCopyWithWrongLengthIsRejected() {
        assertTrue(Matching.lengthMismatch(4 * 60.0, 5 * 60 + 55))        // 4:00 file, real song 5:55 → remix, use YouTube
        assertFalse(Matching.lengthMismatch(5 * 60 + 57.0, 5 * 60 + 55))  // 5:57 file → keep
        assertFalse(Matching.lengthMismatch(4 * 60.0, 0))                 // real length unknown → keep
        assertFalse(Matching.lengthMismatch(0.0, 355))                    // file length unknown → keep
        assertTrue(Matching.lengthMismatch(100.0, 120))                   // short songs: more than 12 s off → reject
        assertFalse(Matching.lengthMismatch(110.0, 120))                  // 10 s off → keep
    }

    // ── Leading "The / A / An" doesn't matter when comparing titles ─────

    @Test fun titleKeyIgnoresLeadingArticle() {
        assertEquals(Matching.titleKey("The Black Sheep"), Matching.titleKey("Black Sheep"))
        assertEquals(Matching.titleKey("A Race Against Time"), Matching.titleKey("race against time"))
        assertFalse(Matching.titleKey("Black Sheep") == Matching.titleKey("Black Sheepdog"))
        assertFalse(Matching.titleKey("Theme") == Matching.titleKey("me"))          // "The" only as a whole word
    }

    @Test fun itunesFindsTitleWithLeadingThe() {
        val r = AlbumInfo("The Black Sheep", "Kataklysm", "Of Ghosts and Gods", "2015", 2, 10, 273, "")
        val got = Itunes.choose(listOf(r), "kataklysm - black sheep")
        assertNotNull(got)
        assertEquals(273, got!!.seconds)                                           // 4:33 target length is known now
        assertNull(Itunes.choose(listOf(r.copy(title = "Black Sheepdog")), "Kataklysm - Black Sheep"))
    }

    @Test fun bandcampMatchesTitleWithLeadingThe() {
        val h = hit("The Black Sheep", "Kataklysm", "kataklysm", "Of Ghosts and Gods")
        assertEquals(listOf(h), BandcampData.choose(listOf(h), "Kataklysm - Black Sheep"))
    }

    @Test fun youTubeFilterIgnoresLeadingArticle() {
        assertEquals(1.0, Matching.titleShare("The Black Sheep", "Kataklysm - Black Sheep (Official Video)"), 0.0)
        assertEquals(1.0, Matching.titleShare("Black Sheep", "Kataklysm - The Black Sheep"), 0.0)
    }

    // ── Proper capitals for all-lowercase lines, Apple's spelling for tags ──

    @Test fun lowercaseLinesGetTitleCase() {
        assertEquals("Kataklysm - The Black Sheep", SongList.titleCaseIfLower("kataklysm - the black sheep"))
        assertEquals("The Weeknd - Blinding Lights", SongList.titleCaseIfLower("the weeknd - blinding lights"))
        assertEquals("Simon & Garfunkel - The Sound of Silence", SongList.titleCaseIfLower("simon & garfunkel - the sound of silence"))
        assertEquals("Metallica - Enter Sandman (Remastered)", SongList.titleCaseIfLower("metallica - enter sandman (remastered)"))
        assertEquals("Guns N' Roses - Sweet Child O' Mine", SongList.titleCaseIfLower("guns n' roses - sweet child o' mine"))
        assertEquals("Ac/Dc - Highway to Hell", SongList.titleCaseIfLower("ac/dc - highway to hell"))
        assertEquals("Song – Title (The Remix)", SongList.titleCaseIfLower("song – title (the remix)"))
    }

    @Test fun linesWithCapitalsAreLeftAlone() {
        assertEquals("PYLOT - a race against time", SongList.titleCaseIfLower("PYLOT - a race against time"))
        assertEquals("deadmau5 - Strobe", SongList.titleCaseIfLower("deadmau5 - Strobe"))
        // The list itself is kept as typed; the official name is worked out per song while downloading.
        assertEquals(listOf("kataklysm - the black sheep", "Perturbator - Future Club"),
            SongList.parse("kataklysm - the black sheep\nPerturbator - Future Club"))
    }

    @Test fun appleSpellingOnlyForTheSameTitleAndArtist() {
        assertEquals("The Black Sheep", Matching.preferAppleTitle("the black sheep", "The Black Sheep"))
        assertEquals("The Black Sheep", Matching.preferAppleTitle("Black Sheep", "The Black Sheep"))
        assertEquals("Black Sheep", Matching.preferAppleTitle("Black Sheep", "Black Sheepdog"))           // different title: keep typed
        assertEquals("The Night They Returned (Remastered)",
            Matching.preferAppleTitle("the night they returned (remastered)", "The Night They Returned (Remastered)"))
        assertEquals("Song (feat. X)", Matching.preferAppleTitle("Song (feat. X)", "Song"))               // never drop what was typed
        assertEquals("AC/DC", Matching.preferAppleArtist("Ac/Dc", "AC/DC"))
        assertEquals("Queen", Matching.preferAppleArtist("Queen", "Queen Mary Band"))
        val apple = AlbumInfo("The Black Sheep (2015 Remaster)", "Kataklysm", "Of Ghosts and Gods", "2015", 2, 10, 273, "")
        assertEquals("The Black Sheep", apple.titleCanon)
        assertEquals("Kataklysm", apple.artistCanon)
    }

    @Test fun accentsDoNotMatter() {
        assertEquals(Matching.norm("Sigur Ros"), Matching.norm("Sigur Rós"))
    }

    // ── Official name from iTunes (file name, checks, tags, log) ────────

    private val blackSheep = AlbumInfo("The Black Sheep", "Kataklysm", "Of Ghosts and Gods", "2015", 2, 10, 273, "")

    /** What the downloader does: iTunes lookup (mocked with one result), then the official name. */
    private fun official(line: String, results: List<AlbumInfo> = listOf(blackSheep)) =
        Matching.officialName(line, Itunes.choose(results, line))

    @Test fun officialNameFromItunes() {
        assertEquals("Kataklysm - The Black Sheep", official("kataklysm - black sheep"))
        assertEquals("Kataklysm - The Black Sheep", official("KATAKLYSM - the BLACK sheep"))
        assertEquals("Kataklysm - The Black Sheep", official("Kataklysm - The Black Sheep"))
    }

    @Test fun otherSongKeepsTypedName() {
        // iTunes only knows a different song: keep the line exactly as typed.
        assertEquals("kataklysm - other song", Matching.officialName("kataklysm - other song", blackSheep))
        assertEquals("Kataklysm - Black Sheepdog", Matching.officialName("Kataklysm - Black Sheepdog", blackSheep))
        assertEquals("Katatonia - The Black Sheep", Matching.officialName("Katatonia - The Black Sheep", blackSheep))
    }

    @Test fun nothingOnItunesTitleCasesLowercaseOnly() {
        assertEquals("Kataklysm - Other Song", Matching.officialName("kataklysm - other song", null))
        assertEquals("kataklysm - Other song", Matching.officialName("kataklysm - Other song", null))
    }

    @Test fun sameSongInOtherCapitalsIsListedTwice() {
        val seen = SeenSongs()
        val first = "kataklysm - black sheep"
        seen.add(first, official(first))
        val second = "KATAKLYSM - the BLACK sheep"
        assertTrue(seen.contains(second) || seen.contains(official(second)))      // reported "listed twice"
        assertTrue(seen.contains("Kataklysm - Black Sheep"))                      // same typed line, other capitals
        assertFalse(seen.contains("Kataklysm - Other Song"))
    }

    @Test fun fileUnderTypedNameCountsAsDownloaded() {
        val typed = "kataklysm - black sheep"
        val inFolder = setOf("kataklysm - black sheep.mp3")                      // saved by an older version
        // The downloader checks the typed name first (before the iTunes lookup), then the official one.
        assertTrue(SongList.safeFileName(typed) + ".mp3" in inFolder)              // skipped, nothing downloaded
        assertEquals("Kataklysm - The Black Sheep.mp3", SongList.safeFileName(official(typed)) + ".mp3")
    }

    // ── Second search when the first finds nothing (desktop v18) ────────

    private fun yt(id: String, title: String, channel: String, sec: Double) = YtEntry(id, id, title, channel, sec)

    /** Mocked YouTube: each query gets its own results; records which queries were searched and how many results were asked for. */
    private class FakeYouTube(val results: Map<String, List<YtEntry>>) {
        val asked = mutableListOf<Pair<String, Int>>()
        suspend fun search(q: String, n: Int): List<YtEntry> { asked += q to n; return results[q].orEmpty() }
    }

    /** First search for "PYLOT - Duel": only unrelated titles, so the title filter leaves nothing. */
    private val unrelated = listOf(
        yt("o1", "PYLOT - Locke", "PYLOT", 260.0),
        yt("o2", "Synthwave Mix 2017", "Retro Lab", 3600.0),
        yt("o3", "Pilot Training Day 1", "Flight Club", 600.0),
    )

    @Test fun secondSearchFindsPylotDuel() = runBlocking {
        val fake = FakeYouTube(mapOf(
            "PYLOT - Duel" to unrelated,                                        // 1st search: nothing usable
            "PYLOT Duel official audio" to listOf(yt("x", "Duel", "Pixies", 200.0), yt("p", "Duel", "PYLOT", 275.0)),
        ))
        val find = Matching.findOnYouTube("PYLOT - Duel", 275) { q, n -> fake.search(q, n) }
        assertTrue(find.extraSearch)
        assertEquals("p", find.ranked.first().second.id)                         // the PYLOT-channel upload
        assertTrue(find.ranked.none { it.second.channel == "Pixies" })           // other artist's "Duel" not accepted
        assertEquals(listOf("PYLOT - Duel" to 8, "PYLOT Duel official audio" to 15), fake.asked)
    }

    @Test fun otherArtistsDuelStillFails() = runBlocking {
        val fake = FakeYouTube(mapOf(
            "PYLOT - Duel" to unrelated,
            "PYLOT Duel official audio" to listOf(yt("x", "Duel", "Some Other Band", 275.0)),
        ))
        val find = Matching.findOnYouTube("PYLOT - Duel", 275) { q, n -> fake.search(q, n) }
        assertTrue(find.ranked.isEmpty())
        assertFalse(find.extraSearch)
        assertEquals(listOf("PYLOT - Duel", "PYLOT Duel official audio", "Duel PYLOT", "PYLOT - Topic Duel"),
            fake.asked.map { it.first })                                         // all three extra wordings tried, in order
    }

    @Test fun songFoundFirstTimeSkipsExtraSearches() = runBlocking {
        val fake = FakeYouTube(mapOf(
            "Perturbator - Future Club" to listOf(yt("f", "Perturbator - Future Club", "Perturbator", 286.0)),
        ))
        val find = Matching.findOnYouTube("Perturbator - Future Club", 286) { q, n -> fake.search(q, n) }
        assertEquals("f", find.ranked.first().second.id)
        assertFalse(find.extraSearch)
        assertEquals(1, fake.asked.size)
    }

    @Test fun extraSearchesUseTitleWithoutBrackets() {
        assertEquals(listOf("Powercyan Space Rock official audio", "Space Rock Powercyan", "Powercyan - Topic Space Rock"),
            Matching.extraQueries("Powercyan - Space Rock (Pylot Remix)"))
        assertTrue(Matching.extraQueries("Duel").isEmpty())                       // no artist: no extra searches
    }
}

package io.github.obamna1234.sevby

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SongListImportTest {

    @Test fun chosicStyleCsv() {
        val csv = "#,Song,Artist,Album,Album Date\n" +
            "1,The Black Sheep,Kataklysm,Of Ghosts and Gods,2015\n" +
            "2,\"Hello, Goodbye\",The Beatles,Magical Mystery Tour,1967\n"
        assertEquals(listOf("Kataklysm - The Black Sheep", "The Beatles - Hello, Goodbye"), SongList.fromExport(csv))
    }

    @Test fun exportifyStyleCsvTakesFirstArtist() {
        val csv = "\"Track URI\",\"Track Name\",\"Artist Name(s)\",\"Album Name\"\n" +
            "\"spotify:track:1\",\"Get Lucky\",\"Daft Punk;Pharrell Williams\",\"Random Access Memories\"\n"
        assertEquals(listOf("Daft Punk - Get Lucky"), SongList.fromExport(csv))
    }

    @Test fun appleMusicTabSeparated() {
        val tsv = "Name\tArtist\tAlbum\nFuture Club\tPerturbator\tDangerous Days\n"
        assertEquals(listOf("Perturbator - Future Club"), SongList.fromExport(tsv))
    }

    @Test fun m3uPlaylist() {
        val m3u = "#EXTM3U\n#EXTINF:273,Kataklysm - The Black Sheep\n/music/a.mp3\n"
        assertEquals(listOf("Kataklysm - The Black Sheep"), SongList.fromExport(m3u))
    }

    @Test fun plainListsAreLeftAlone() {
        assertNull(SongList.fromExport("Crosby, Stills & Nash - Ohio\nKataklysm - The Black Sheep"))
        assertNull(SongList.fromExport("Kataklysm - The Black Sheep\nPerturbator - Future Club"))
        assertNull(SongList.fromExport("a,b,c\n1,2,3"))                     // CSV without title/artist columns
    }

    @Test fun utf16FilesAreRead() {
        val bytes = "﻿Name\tArtist\nStrobe\tdeadmau5\n".toByteArray(Charsets.UTF_16LE)
        assertEquals(listOf("deadmau5 - Strobe"), SongList.fromExport(SongList.decode(bytes)))
        assertEquals("Kataklysm - The Black Sheep", SongList.decode("﻿Kataklysm - The Black Sheep".toByteArray()))
    }
}

#!/usr/bin/env python
#
# -----------------------------------------------------------------------------
# Copyright (c) 2017 The Regents of the University of California
#
# This file is part of kevlar (http://github.com/dib-lab/kevlar) and is
# licensed under the MIT license: see LICENSE.
# -----------------------------------------------------------------------------

import khmer
import kevlar
import re


class Variant(object):
    """Base class for handling variant calls and no-calls."""

    def __init__(self, seqid, pos, refr, alt, **kwargs):
        """
        Constructor method.

        The `pos` parameter expects the genomic position as a 0-based index.
        Setting the `refr` or `alt` parameters to `.` will designate this
        variant as a "no call".
        """
        self._seqid = seqid
        self._pos = pos
        self._refr = refr
        self._alt = alt
        self.info = dict()
        for key, value in kwargs.items():
            self.info[key] = value

    @property
    def seqid(self):
        return self._seqid

    @property
    def position(self):
        return self._pos

    @property
    def vcf(self):
        """Print variant to VCF."""
        attrstr = '.'
        if len(self.info) > 0:
            kvpairs = list()
            for key in sorted(self.info):
                if key != 'QS':
                    kvpairs.append(self.attribute(key, pair=True))
            queryseq = self.attribute('QS', pair=True)
            if queryseq:
                kvpairs.append(queryseq)
            attrstr = ';'.join(kvpairs)

        filterstr = 'PASS' if self._refr != '.' else '.'
        return '{:s}\t{:d}\t.\t{:s}\t{:s}\t.\t{:s}\t{:s}'.format(
            self._seqid, self._pos + 1, self._refr, self._alt, filterstr,
            attrstr
        )

    @property
    def cigar(self):
        return self.attribute('CG')

    @property
    def window(self):
        """
        Getter method for the variant window.

        The "variant window" (abbreviated `VW` in VCF output) is the sequence
        interval in the proband contig that encompasses all k-mers overlapping
        the variant.

        GCCTAGTTAGCTAACGTCCCGATCACTGTGTCACTGC
                    .....A
                     ....A.
                      ...A..
                       ..A...
                        .A....
                         A.....
                         |        <-- position of variant
                    [---------]   <-- variant window, interval (inclusive)
                                      encompassing all 6-mers that overlap the
                                      variant
        """
        return self.attribute('VW')

    @property
    def refrwindow(self):
        """Similar to `window`, but encapsulating the reference allele."""
        return self.attribute('RW')

    def attribute(self, key, pair=False):
        if key not in self.info:
            return None
        value = self.info[key].replace(';', ':')
        if pair:
            keyvaluepair = '{:s}={:s}'.format(key, value)
            return keyvaluepair
        else:
            return value

    @property
    def genotypes(self):
        gt = self.attribute('GT')
        if not gt:
            return None
        return tuple(gt.split(','))


class VariantSNV(Variant):
    def __str__(self):
        return '{:s}:{:d}:{:s}->{:s}'.format(self._seqid, self._pos,
                                             self._refr, self._alt)


class VariantIndel(Variant):
    def __str__(self):
        """
        Return a string representation of this variant.

        The reason that 1 is added to the variant position is to offset the
        nucleotide shared by the reference and alternate alleles. This position
        is still 0-based (as opposed to VCF's 1-based coordinate system) but
        does not include the shared nucleotide.
        """
        pos = self._pos + 1
        if len(self._refr) > len(self._alt):
            dellength = len(self._refr) - len(self._alt)
            return '{:s}:{:d}:{:d}D'.format(self._seqid, pos, dellength)
        else:
            insertion = self._alt[1:]
            return '{:s}:{:d}:I->{:s}'.format(self._seqid, pos, insertion)


def local_to_global(localcoord, subseqid):
    match = re.search('(\S+)_(\d+)-(\d+)', subseqid)
    assert match, 'unable to parse subseqid {:s}'.format(subseqid)
    seqid = match.group(1)
    globaloffset = int(match.group(2))
    globalcoord = globaloffset + localcoord
    return seqid, globalcoord


def call_snv(target, query, offset, length, ksize):
    targetshort = False
    if offset < 0:
        offset *= -1
        gdnaoffset = 0
        targetshort = True
        t = target.sequence[:length]
        q = query.sequence[offset:offset+length]
    else:
        gdnaoffset = offset
        t = target.sequence[offset:offset+length]
        q = query.sequence[:length]
    diffs = [(i, t[i], q[i]) for i in range(length) if t[i] != q[i]]
    if len(diffs) == 0:
        seqid, globalcoord = local_to_global(gdnaoffset, target.name)
        nocall = Variant(seqid, globalcoord, '.', '.', NC='perfectmatch',
                         QN=query.name, QS=q)
        return [nocall]

    snvs = list()
    for diff in diffs:
        minpos = max(diff[0] - ksize + 1, 0)
        maxpos = min(diff[0] + ksize, length)
        window = q[minpos:maxpos]
        refrwindow = t[minpos:maxpos]

        # numoverlappingkmers = len(window) - ksize + 1
        # kmers = [window[i:i+ksize] for i in range(numoverlappingkmers)]
        refr = diff[1].upper()
        alt = diff[2].upper()
        localcoord = diff[0]
        if not targetshort:
            localcoord += offset
        seqid, globalcoord = local_to_global(localcoord, target.name)
        snv = VariantSNV(seqid, globalcoord, refr, alt, VW=window,
                         RW=refrwindow, IK=str(len(query.ikmers)))
        snvs.append(snv)
    return snvs


def deletion_allele(target, query, offset, ksize, leftmatch, indellength):
    minpos = leftmatch - ksize + 1
    maxpos = leftmatch + ksize - 1
    altwindow = query.sequence[minpos:maxpos]
    minpos += offset
    maxpos += offset + indellength
    refrwindow = target.sequence[minpos:maxpos]

    refr = target.sequence[offset+leftmatch-1:offset+leftmatch+indellength]
    alt = refr[0]
    return refr, alt, refrwindow, altwindow


def insertion_allele(target, query, offset, ksize, leftmatch, indellength):
    minpos = leftmatch - ksize + 1
    maxpos = leftmatch + ksize + indellength - 1
    altwindow = query.sequence[minpos:maxpos]
    minpos += offset
    maxpos += offset - indellength
    refrwindow = target.sequence[minpos:maxpos]

    alt = query.sequence[leftmatch-1:leftmatch+indellength]
    refr = alt[0]
    return refr, alt, refrwindow, altwindow


def call_deletion(target, query, offset, ksize, leftmatch, indellength):
    if offset < 0:
        offset *= -1
        targetshort = True
        alt, refr, altwindow, refrwindow = insertion_allele(
            query, target, offset, ksize, leftmatch, indellength
        )
    else:
        targetshort = False
        refr, alt, refrwindow, altwindow = deletion_allele(
            target, query, offset, ksize, leftmatch, indellength
        )
    # This assertion is no longer valid when query is longer than target
    # assert len(refr) == indellength + 1
    localcoord = leftmatch
    if not targetshort:
        localcoord += offset
    seqid, globalcoord = local_to_global(localcoord, target.name)
    return [VariantIndel(seqid, globalcoord - 1, refr, alt, VW=altwindow,
                         RW=refrwindow, IK=str(len(query.ikmers)))]


def call_insertion(target, query, offset, ksize, leftmatch, indellength):
    if offset < 0:
        offset *= -1
        targetshort = True
        alt, refr, altwindow, refrwindow = deletion_allele(
            query, target, offset, ksize, leftmatch, indellength
        )
    else:
        targetshort = False
        refr, alt, refrwindow, altwindow = insertion_allele(
            target, query, offset, ksize, leftmatch, indellength
        )

    assert len(alt) == indellength + 1
    localcoord = leftmatch
    if not targetshort:
        localcoord += offset
    seqid, globalcoord = local_to_global(localcoord, target.name)
    return [VariantIndel(seqid, globalcoord - 1, refr, alt, VW=altwindow,
                         RW=refrwindow, IK=str(len(query.ikmers)))]


def make_call(target, query, cigar, ksize):
    snvmatch = re.search('^(\d+)([DI])(\d+)M(\d+)[DI]$', cigar)
    snvmatch2 = re.search('^(\d+)([DI])(\d+)M(\d+)[DI](\d+)M$', cigar)
    if snvmatch:
        offset = int(snvmatch.group(1))
        if snvmatch.group(2) == 'I':
            offset *= -1
        length = int(snvmatch.group(3))
        return call_snv(target, query, offset, length, ksize)
    elif snvmatch2 and int(snvmatch2.group(5)) <= 5:
        offset = int(snvmatch2.group(1))
        if snvmatch2.group(2) == 'I':
            offset *= -1
        length = int(snvmatch2.group(3))
        return call_snv(target, query, offset, length, ksize)

    indelmatch = re.search(
        '^(\d+)([DI])(\d+)M(\d+)([ID])(\d+)M(\d+)[DI]$', cigar
    )
    indelmatch2 = re.search(
        '^(\d+)([DI])(\d+)M(\d+)([ID])(\d+)M(\d+)[DI](\d+)M$', cigar
    )
    if indelmatch:
        offset = int(indelmatch.group(1))
        if indelmatch.group(2) == 'I':
            offset *= -1
        leftmatch = int(indelmatch.group(3))
        indellength = int(indelmatch.group(4))
        indeltype = indelmatch.group(5)
        callfunc = call_deletion if indeltype == 'D' else call_insertion
        return callfunc(target, query, offset, ksize, leftmatch, indellength)
    elif indelmatch2 and int(indelmatch2.group(8)) <= 5:
        offset = int(indelmatch2.group(1))
        if indelmatch2.group(2) == 'I':
            offset *= -1
        leftmatch = int(indelmatch2.group(3))
        indellength = int(indelmatch2.group(4))
        indeltype = indelmatch2.group(5)
        callfunc = call_deletion if indeltype == 'D' else call_insertion
        return callfunc(target, query, offset, ksize, leftmatch, indellength)

    seqid, globalcoord = local_to_global(0, target.name)
    nocall = Variant(seqid, globalcoord, '.', '.', NC='inscrutablecigar',
                     QN=query.name, QS=query.sequence, CG=cigar)
    return [nocall]


def align_mates(matefile, refrfile):
    cmd = 'bwa mem {:s} {:s}'.format(refrfile, matefile)
    cmdargs = cmd.split()
    for seqid, pos in kevlar.bwa_align(cmdargs):
        yield seqid, pos


def mate_distance(mate_positions, gdna_position):
    gdnaseq, gdnapos = gdna_position
    distances = list()
    for seqid, pos in mate_positions:
        if seqid != gdnaseq:
            continue
        d = abs(pos - gdnapos)
        distances.append(d)
    return sum(distances) / len(distances)


def align_both_strands(targetseq, queryseq, match=1, mismatch=2, gapopen=5,
                       gapextend=0):
    cigar1, score1 = kevlar.align(targetseq, queryseq, match, mismatch,
                                  gapopen, gapextend)
    cigar2, score2 = kevlar.align(targetseq, kevlar.revcom(queryseq), match,
                                  mismatch, gapopen, gapextend)

    if score2 > score1:
        cigar = cigar2
        score = score2
        strand = -1
    else:
        cigar = cigar1
        score = score1
        strand = 1
    return cigar, score, strand


def alignment_interpretable(cigar):
    patterns = [
        '^(\d+)([DI])(\d+)M(\d+)[DI]$',
        '^(\d+)([DI])(\d+)M(\d+)[DI](\d+)M$',
        '^(\d+)([DI])(\d+)M(\d+)([ID])(\d+)M(\d+)[DI]$',
        '^(\d+)([DI])(\d+)M(\d+)([ID])(\d+)M(\d+)[DI](\d+)M$',
    ]
    for pattern in patterns:
        if re.search(pattern, cigar) is not None:
            return True
    return False


def call(targetlist, querylist, match=1, mismatch=2, gapopen=5,
         gapextend=0, ksize=31, matefile=None, refrfile=None):
    """
    Wrap the `kevlar call` procedure as a generator function.

    Input is the following.
    - an iterable containing one or more target sequences from the reference
      genome, stored as khmer or screed sequence records
    - an iterable containing one or more contigs assembled by kevlar, stored as
      khmer or screed sequence records
    - alignment match score (integer)
    - alignment mismatch penalty (integer)
    - alignment gap open penalty (integer)
    - alignment gap extension penalty (integer)
    - mates of interesting reads, in case these are needed to distinguish
      between multiple best hist (filename)
    - reference file to which mates will be mapped

    The function yields tuples of target sequence name, query sequence name,
    and alignment CIGAR string
    """
    mate_pos = None
    for query in sorted(querylist, reverse=True, key=len):
        alignments = list()
        for target in sorted(targetlist, key=lambda record: record.name):
            cigar, score, strand = align_both_strands(
                target.sequence, query.sequence, match, mismatch, gapopen,
                gapextend
            )
            alignments.append((target, cigar, score, strand))
        alignments.sort(key=lambda a: a[2], reverse=True)
        if len(alignments) == 1:
            aligns2report = alignments
        else:
            scrtbl = [a for a in alignments if alignment_interpretable(a[1])]
            if len(scrtbl) == 0:
                finallist = alignments
            else:
                finallist = scrtbl
            bestscore = finallist[0][2]
            aligns2report = [a for a in finallist if a[2] == bestscore]

        if len(aligns2report) > 1:
            if matefile and refrfile:
                if mate_pos is None:
                    mate_pos = list(align_mates(matefile, refrfile))
                aligns2report.sort(key=lambda a: mate_distance(mate_pos, local_to_global(0, a[0].name)))

        best = True
        for alignment in aligns2report:
            besttarget, bestcigar, bestscore, bestorientation = alignment
            if bestorientation == -1:
                query.sequence = kevlar.revcom(query.sequence)
            for varcall in make_call(besttarget, query, bestcigar, ksize):
                if mate_pos and not best:
                    varcall.info['NC'] = 'matefail'
                yield varcall
            if bestorientation == -1:
                # Change it back!
                # There's a better way to do this, but this works for now.
                query.sequence = kevlar.revcom(query.sequence)
            best = False


def main(args):
    outstream = kevlar.open(args.out, 'w')
    qinstream = kevlar.parse_augmented_fastx(kevlar.open(args.queryseq, 'r'))
    queryseqs = list(qinstream)
    targetseqs = list(khmer.ReadParser(args.targetseq))
    caller = call(
        targetseqs, queryseqs,
        args.match, args.mismatch, args.open, args.extend,
        args.ksize,
    )
    for varcall in caller:
        print(varcall.vcf, file=outstream)

import org.apache.lucene.analysis.standard.StandardAnalyzer;
import org.apache.lucene.document.Document;
import org.apache.lucene.document.Field;
import org.apache.lucene.document.IntPoint;
import org.apache.lucene.document.StoredField;
import org.apache.lucene.document.StringField;
import org.apache.lucene.document.TextField;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.index.IndexWriter;
import org.apache.lucene.index.IndexWriterConfig;
import org.apache.lucene.index.Term;
import org.apache.lucene.search.BooleanClause;
import org.apache.lucene.search.BooleanQuery;
import org.apache.lucene.search.IndexSearcher;
import org.apache.lucene.search.PhraseQuery;
import org.apache.lucene.search.Query;
import org.apache.lucene.search.ScoreDoc;
import org.apache.lucene.search.TermQuery;
import org.apache.lucene.search.TopDocs;
import org.apache.lucene.store.ByteBuffersDirectory;

public final class LuceneBench {
    static final int WORDS = 4000;
    static final int DOCUMENTS = 20_000;
    static final int DOCUMENT_WORDS = 60;
    static final int QUERIES = 400;

    static final class Lcg {
        long seed;

        Lcg(long seed) {
            this.seed = seed;
        }

        long advance() {
            seed = seed * 6364136223846793005L + 1442695040888963407L;
            return seed;
        }

        int next(int bound) {
            return (int) ((advance() >>> 33) % bound);
        }
    }

    static final String[] VOCABULARY = new String[WORDS];

    static String[] corpus() {
        Lcg random = new Lcg(42);
        for (int i = 0; i < WORDS; i++) {
            int length = 3 + random.next(7);
            StringBuilder word = new StringBuilder();
            for (int j = 0; j < length; j++) {
                word.append((char) ('a' + random.next(26)));
            }
            VOCABULARY[i] = word.append(i).toString();
        }
        String[] texts = new String[DOCUMENTS];
        for (int d = 0; d < DOCUMENTS; d++) {
            StringBuilder text = new StringBuilder();
            for (int w = 0; w < DOCUMENT_WORDS; w++) {
                int r = random.next(WORDS);
                text.append(VOCABULARY[(r * r) / WORDS]).append(' ');
            }
            texts[d] = text.toString();
        }
        return texts;
    }

    static long round(String[] texts) throws Exception {
        long sum = 0;
        try (ByteBuffersDirectory directory = new ByteBuffersDirectory()) {
            IndexWriterConfig config = new IndexWriterConfig(new StandardAnalyzer()).setRAMBufferSizeMB(64);
            try (IndexWriter writer = new IndexWriter(directory, config)) {
                for (int i = 0; i < texts.length; i++) {
                    Document doc = new Document();
                    doc.add(new StringField("id", Integer.toString(i), Field.Store.YES));
                    doc.add(new IntPoint("n", i));
                    doc.add(new StoredField("n", i));
                    doc.add(new TextField("body", texts[i], Field.Store.NO));
                    writer.addDocument(doc);
                }
                writer.commit();
            }
            try (DirectoryReader reader = DirectoryReader.open(directory)) {
                IndexSearcher searcher = new IndexSearcher(reader);
                Lcg random = new Lcg(7);
                for (int q = 0; q < QUERIES; q++) {
                    long state = random.advance();
                    int a = (int) ((state >>> 33) % 200);
                    int b = (int) ((state >>> 13) % 400);
                    Query query = switch (q & 3) {
                        case 0 -> new TermQuery(new Term("body", VOCABULARY[a]));
                        case 1 -> new BooleanQuery.Builder()
                                .add(new TermQuery(new Term("body", VOCABULARY[a])), BooleanClause.Occur.MUST)
                                .add(new TermQuery(new Term("body", VOCABULARY[b])), BooleanClause.Occur.MUST)
                                .build();
                        case 2 -> new PhraseQuery("body", VOCABULARY[a], VOCABULARY[b]);
                        default -> IntPoint.newRangeQuery("n", a * 50, a * 50 + 2000);
                    };
                    TopDocs top = searcher.search(query, 10);
                    sum += top.totalHits.value();
                    for (ScoreDoc hit : top.scoreDocs) {
                        sum += hit.doc;
                    }
                }
            }
        }
        return sum;
    }

    public static void main(String[] args) throws Exception {
        int rounds = args.length > 0 ? Integer.parseInt(args[0]) : 15;
        String[] texts = corpus();
        long sink = 0;
        for (int r = 0; r < rounds; r++) {
            long start = System.nanoTime();
            sink += round(texts);
            long elapsed = System.nanoTime() - start;
            System.out.println("round " + r + " " + elapsed / 1000 / 1000.0 + " ms");
        }
        System.out.println("checksum " + sink);
    }
}

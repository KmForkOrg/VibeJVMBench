import java.util.ArrayList;
import java.util.List;

public final class GameTick {
    interface Entity {
        void tick(int[] chunk, int tick);

        long state();
    }

    static class Mob implements Entity {
        int x;
        int z;
        int energy;

        Mob(int seed) {
            x = seed & 255;
            z = (seed * 7) & 255;
            energy = seed & 255;
        }

        @Override
        public void tick(int[] chunk, int tick) {
            x = (x + 3 + (energy & 3)) & 255;
            z = (z + tick + 1) & 255;
            int cell = (z << 8) | x;
            energy += chunk[cell];
            chunk[cell] = (energy >>> 3) & 255;
        }

        @Override
        public long state() {
            return ((long) x << 40) + ((long) z << 32) + energy;
        }
    }

    static final class Player extends Mob {
        Player(int seed) {
            super(seed);
        }

        @Override
        public void tick(int[] chunk, int tick) {
            super.tick(chunk, tick);
            energy ^= chunk[(z << 8) | x] ^ tick;
        }
    }

    static final class Item extends Mob {
        Item(int seed) {
            super(seed);
        }

        @Override
        public void tick(int[] chunk, int tick) {
            if (tick % 4 == 0) {
                super.tick(chunk, tick);
            } else {
                energy--;
            }
        }
    }

    static void ticks(List<Entity> entities, int[] chunk) {
        for (int tick = 0; tick < 128; tick++) {
            for (Entity entity : entities) {
                entity.tick(chunk, tick);
            }
            for (int cell = tick; cell < chunk.length; cell += 128) {
                chunk[cell] = (chunk[cell] + tick) & 255;
            }
        }
    }

    static long run(int seed) {
        List<Entity> entities = new ArrayList<>();
        for (int i = 0; i < 1024; i++) {
            entities.add(switch (i % 3) {
                case 0 -> new Player(i + seed);
                case 1 -> new Item(i + seed);
                default -> new Mob(i + seed);
            });
        }
        int[] chunk = new int[256 * 256];
        for (int i = 0; i < chunk.length; i++) {
            chunk[i] = (i ^ seed) & 255;
        }
        ticks(entities, chunk);
        long sum = 0;
        for (Entity entity : entities) {
            sum += entity.state();
        }
        for (int cell : chunk) {
            sum = sum * 31 + cell;
        }
        return sum;
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 1100, GameTick::run);
    }
}

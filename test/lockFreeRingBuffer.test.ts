import { describe, it, expect, beforeEach, jest } from "@jest/globals";
import {
  LockFreeRingBuffer,
  MPSCRingBuffer,
  LockFreeIngestionChannel,
  LockFreeChannelManager,
} from "../src/queue/lockFreeRingBuffer";
import { IngestionPacket, PacketPriority } from "../src/queue/backpressure";

describe("LockFreeRingBuffer", () => {
  let buffer: LockFreeRingBuffer<IngestionPacket>;

  beforeEach(() => {
    buffer = new LockFreeRingBuffer<IngestionPacket>({
      capacity: 16,
      enableMetrics: true,
      enableBatching: false,
      batchSize: 1,
    });
  });

  describe("Basic Operations", () => {
    it("should enqueue and dequeue items", () => {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: { test: "data" },
        timestamp: Date.now(),
      };

      expect(buffer.tryEnqueue(packet)).toBe(true);
      expect(buffer.size()).toBe(1);

      const dequeued = buffer.tryDequeue();
      expect(dequeued).toEqual(packet);
      expect(buffer.size()).toBe(0);
    });

    it("should return false when buffer is full", () => {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: { test: "data" },
        timestamp: Date.now(),
      };

      // Fill buffer
      for (let i = 0; i < 16; i++) {
        expect(buffer.tryEnqueue(packet)).toBe(true);
      }

      // Should fail on 17th item
      expect(buffer.tryEnqueue(packet)).toBe(false);
    });

    it("should return undefined when buffer is empty", () => {
      expect(buffer.tryDequeue()).toBeUndefined();
    });

    it("should correctly report empty status", () => {
      expect(buffer.isEmpty()).toBe(true);
      buffer.tryEnqueue({
        priority: PacketPriority.STANDARD,
        data: {},
        timestamp: Date.now(),
      });
      expect(buffer.isEmpty()).toBe(false);
    });

    it("should correctly report full status", () => {
      expect(buffer.isFull()).toBe(false);
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: {},
        timestamp: Date.now(),
      };

      for (let i = 0; i < 16; i++) {
        buffer.tryEnqueue(packet);
      }
      expect(buffer.isFull()).toBe(true);
    });
  });

  describe("FIFO Order", () => {
    it("should maintain FIFO order", () => {
      const packets: IngestionPacket[] = [
        { priority: PacketPriority.STANDARD, data: { id: 1 }, timestamp: Date.now() },
        { priority: PacketPriority.STANDARD, data: { id: 2 }, timestamp: Date.now() },
        { priority: PacketPriority.STANDARD, data: { id: 3 }, timestamp: Date.now() },
      ];

      packets.forEach(p => buffer.tryEnqueue(p));

      for (let i = 0; i < packets.length; i++) {
        const dequeued = buffer.tryDequeue();
        expect(dequeued?.data.id).toBe(packets[i].data.id);
      }
    });
  });

  describe("Metrics", () => {
    it("should track enqueue/dequeue counts", () => {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: {},
        timestamp: Date.now(),
      };

      buffer.tryEnqueue(packet);
      buffer.tryEnqueue(packet);
      buffer.tryDequeue();

      const metrics = buffer.getMetrics();
      expect(metrics.totalEnqueued).toBe(2);
      expect(metrics.totalDequeued).toBe(1);
    });

    it("should track failures", () => {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: {},
        timestamp: Date.now(),
      };

      // Fill buffer
      for (let i = 0; i < 16; i++) {
        buffer.tryEnqueue(packet);
      }

      // Try to enqueue when full
      buffer.tryEnqueue(packet);
      buffer.tryEnqueue(packet);

      const metrics = buffer.getMetrics();
      expect(metrics.enqueueFailures).toBe(2);
    });

    it("should calculate utilization correctly", () => {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: {},
        timestamp: Date.now(),
      };

      buffer.tryEnqueue(packet);
      buffer.tryEnqueue(packet);

      const metrics = buffer.getMetrics();
      expect(metrics.utilization).toBe(2 / 16);
    });

    it("should reset metrics", () => {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: {},
        timestamp: Date.now(),
      };

      buffer.tryEnqueue(packet);
      buffer.resetMetrics();

      const metrics = buffer.getMetrics();
      expect(metrics.totalEnqueued).toBe(0);
      expect(metrics.totalDequeued).toBe(0);
    });
  });

  describe("Power of 2 Capacity", () => {
    it("should round capacity to next power of 2", () => {
      const buffer = new LockFreeRingBuffer<IngestionPacket>({
        capacity: 15,
        enableMetrics: true,
        enableBatching: false,
        batchSize: 1,
      });

      const metrics = buffer.getMetrics();
      expect(metrics.capacity).toBe(16);
    });

    it("should handle capacity of 1", () => {
      const buffer = new LockFreeRingBuffer<IngestionPacket>({
        capacity: 1,
        enableMetrics: true,
        enableBatching: false,
        batchSize: 1,
      });

      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: {},
        timestamp: Date.now(),
      };

      expect(buffer.tryEnqueue(packet)).toBe(true);
      expect(buffer.tryEnqueue(packet)).toBe(false);
    });
  });
});

describe("MPSCRingBuffer", () => {
  let buffer: MPSCRingBuffer<IngestionPacket>;

  beforeEach(() => {
    buffer = new MPSCRingBuffer<IngestionPacket>({
      capacity: 16,
      enableMetrics: true,
      enableBatching: false,
      batchSize: 1,
    });
  });

  describe("Basic Operations", () => {
    it("should enqueue and dequeue items", () => {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: { test: "data" },
        timestamp: Date.now(),
      };

      expect(buffer.tryEnqueue(packet)).toBe(true);
      expect(buffer.size()).toBe(1);

      const dequeued = buffer.tryDequeue();
      expect(dequeued).toEqual(packet);
      expect(buffer.size()).toBe(0);
    });

    it("should handle batch dequeue", () => {
      const packets: IngestionPacket[] = [
        { priority: PacketPriority.STANDARD, data: { id: 1 }, timestamp: Date.now() },
        { priority: PacketPriority.STANDARD, data: { id: 2 }, timestamp: Date.now() },
        { priority: PacketPriority.STANDARD, data: { id: 3 }, timestamp: Date.now() },
      ];

      packets.forEach(p => buffer.tryEnqueue(p));

      const batch = buffer.tryDequeueBatch(2);
      expect(batch.length).toBe(2);
      expect(batch[0].data.id).toBe(1);
      expect(batch[1].data.id).toBe(2);
    });

    it("should return empty batch when buffer is empty", () => {
      const batch = buffer.tryDequeueBatch(10);
      expect(batch.length).toBe(0);
    });
  });

  describe("Metrics", () => {
    it("should track batch processing", () => {
      const packets: IngestionPacket[] = [
        { priority: PacketPriority.STANDARD, data: {}, timestamp: Date.now() },
        { priority: PacketPriority.STANDARD, data: {}, timestamp: Date.now() },
      ];

      packets.forEach(p => buffer.tryEnqueue(p));
      buffer.tryDequeueBatch(2);

      const metrics = buffer.getMetrics();
      expect(metrics.batchesProcessed).toBe(1);
    });
  });
});

describe("LockFreeIngestionChannel", () => {
  let channel: LockFreeIngestionChannel;

  beforeEach(() => {
    channel = new LockFreeIngestionChannel("test-channel", {
      capacity: 16,
      enableMetrics: true,
      enableBatching: true,
      batchSize: 4,
    });
  });

  describe("Publish/Subscribe", () => {
    it("should publish and subscribe to packets", () => {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: { test: "data" },
        timestamp: Date.now(),
      };

      expect(channel.publish(packet)).toBe(true);

      const packets = channel.subscribe(1);
      expect(packets.length).toBe(1);
      expect(packets[0]).toEqual(packet);
    });

    it("should return false when channel is full", () => {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: {},
        timestamp: Date.now(),
      };

      for (let i = 0; i < 16; i++) {
        channel.publish(packet);
      }

      expect(channel.publish(packet)).toBe(false);
    });

    it("should use default batch size when not specified", () => {
      const packets: IngestionPacket[] = [
        { priority: PacketPriority.STANDARD, data: { id: 1 }, timestamp: Date.now() },
        { priority: PacketPriority.STANDARD, data: { id: 2 }, timestamp: Date.now() },
      ];

      packets.forEach(p => channel.publish(p));
      const subscribed = channel.subscribe();
      expect(subscribed.length).toBeLessThanOrEqual(4);
    });
  });

  describe("Channel Info", () => {
    it("should return channel name", () => {
      expect(channel.getName()).toBe("test-channel");
    });

    it("should return channel size", () => {
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: {},
        timestamp: Date.now(),
      };

      channel.publish(packet);
      channel.publish(packet);

      expect(channel.size()).toBe(2);
    });
  });
});

describe("LockFreeChannelManager", () => {
  let manager: LockFreeChannelManager;

  beforeEach(() => {
    manager = new LockFreeChannelManager({
      capacity: 16,
      enableMetrics: true,
      enableBatching: true,
      batchSize: 4,
    });
  });

  describe("Channel Management", () => {
    it("should create new channel", () => {
      const channel = manager.getChannel("new-channel");
      expect(channel).toBeDefined();
      expect(channel.getName()).toBe("new-channel");
    });

    it("should return existing channel", () => {
      const channel1 = manager.getChannel("shared-channel");
      const channel2 = manager.getChannel("shared-channel");
      expect(channel1).toBe(channel2);
    });

    it("should remove channel", () => {
      manager.getChannel("temp-channel");
      expect(manager.removeChannel("temp-channel")).toBe(true);
      expect(manager.removeChannel("temp-channel")).toBe(false);
    });

    it("should return all channel names", () => {
      manager.getChannel("channel1");
      manager.getChannel("channel2");
      manager.getChannel("channel3");

      const names = manager.getChannelNames();
      expect(names).toContain("channel1");
      expect(names).toContain("channel2");
      expect(names).toContain("channel3");
    });
  });

  describe("Aggregate Metrics", () => {
    it("should calculate aggregate metrics", () => {
      const channel1 = manager.getChannel("channel1");
      const channel2 = manager.getChannel("channel2");

      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: {},
        timestamp: Date.now(),
      };

      channel1.publish(packet);
      channel2.publish(packet);
      channel2.publish(packet);

      const aggregate = manager.getAggregateMetrics();
      expect(aggregate.totalSize).toBe(3);
      expect(aggregate.totalCapacity).toBe(32);
      expect(aggregate.averageUtilization).toBe(3 / 32);
    });

    it("should handle empty manager", () => {
      const aggregate = manager.getAggregateMetrics();
      expect(aggregate.totalSize).toBe(0);
      expect(aggregate.totalCapacity).toBe(0);
      expect(aggregate.averageUtilization).toBe(0);
    });
  });

  describe("Metrics Collection", () => {
    it("should get all metrics", () => {
      manager.getChannel("channel1");
      manager.getChannel("channel2");

      const allMetrics = manager.getAllMetrics();
      expect(allMetrics.size).toBe(2);
      expect(allMetrics.has("channel1")).toBe(true);
      expect(allMetrics.has("channel2")).toBe(true);
    });

    it("should reset all metrics", () => {
      const channel = manager.getChannel("channel1");
      const packet: IngestionPacket = {
        priority: PacketPriority.STANDARD,
        data: {},
        timestamp: Date.now(),
      };

      channel.publish(packet);
      manager.resetAllMetrics();

      const metrics = channel.getMetrics();
      expect(metrics.totalEnqueued).toBe(0);
    });
  });

  describe("Shutdown", () => {
    it("should clear all channels", () => {
      manager.getChannel("channel1");
      manager.getChannel("channel2");

      manager.shutdown();

      expect(manager.getChannelNames().length).toBe(0);
    });
  });
});

describe("Concurrency Tests", () => {
  it("should handle rapid enqueue/dequeue cycles", () => {
    const buffer = new LockFreeRingBuffer<number>({
      capacity: 1024,
      enableMetrics: true,
      enableBatching: false,
      batchSize: 1,
    });

    const iterations = 10000;
    for (let i = 0; i < iterations; i++) {
      expect(buffer.tryEnqueue(i)).toBe(true);
      expect(buffer.tryDequeue()).toBe(i);
    }

    expect(buffer.size()).toBe(0);
  });

  it("should handle buffer wraparound", () => {
    const buffer = new LockFreeRingBuffer<number>({
      capacity: 8,
      enableMetrics: true,
      enableBatching: false,
      batchSize: 1,
    });

    // Fill and drain multiple times to test wraparound
    for (let cycle = 0; cycle < 10; cycle++) {
      for (let i = 0; i < 8; i++) {
        buffer.tryEnqueue(cycle * 8 + i);
      }
      for (let i = 0; i < 8; i++) {
        expect(buffer.tryDequeue()).toBe(cycle * 8 + i);
      }
    }

    expect(buffer.size()).toBe(0);
  });
});

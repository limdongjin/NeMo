import random
import torch
import numpy as np
from torch.utils.data import Dataset
from nemo.collections.nlp.data.language_modeling.megatron.megatron_batch_samplers import BaseMegatronBatchSampler
from nemo.collections.vision.data.megatron.vit_dataset import RandomSeedDataset


class MegatronVisionPretrainingRandomBatchSampler(BaseMegatronBatchSampler):

    def __init__(
        self,
        dataset: Dataset,
        total_samples: int,
        consumed_samples: int,
        micro_batch_size: int,
        global_batch_size: int,
        data_parallel_rank: int,
        data_parallel_size: int,
        drop_last: bool,
        data_sharding: bool,
    ) -> None:
        super().__init__(
            total_samples=total_samples,
            consumed_samples=consumed_samples,
            micro_batch_size=micro_batch_size,
            global_batch_size=global_batch_size,
            data_parallel_rank=data_parallel_rank,
            data_parallel_size=data_parallel_size,
            drop_last=drop_last,
        )
        self.dataset = dataset
        self.data_sharding = data_sharding
        self.last_batch_size = self.total_samples % self.global_batch_size

    def __len__(self):
        num_available_samples = self.total_samples
        if self.drop_last:
            return num_available_samples // self.global_batch_size
        else:
            return (num_available_samples + self.global_batch_size - 1) // self.global_batch_size

    def __iter__(self):
        active_total_samples = self.total_samples - self.last_batch_size
        self.epoch = self.consumed_samples // active_total_samples
        current_epoch_samples = self.consumed_samples % active_total_samples
        assert current_epoch_samples % (self.micro_batch_size * self.data_parallel_size) == 0

        if isinstance(self.dataset, RandomSeedDataset):
            self.dataset.set_epoch(self.epoch)

        # data sharding and random sampling
        if self.data_sharding:
            bucket_size = (self.total_samples // (self.micro_batch_size * self.data_parallel_size)) \
                          * self.micro_batch_size
            bucket_offset = current_epoch_samples // self.data_parallel_size
            start_idx = self.data_parallel_rank * bucket_size

            print(len(self.dataset), self.epoch, self.dataset.curr_seed, active_total_samples, current_epoch_samples, bucket_size, bucket_offset, start_idx)

            g = torch.Generator()
            g.manual_seed(self.epoch)
            random_idx = torch.randperm(bucket_size, generator=g).tolist()
            idx_range = [start_idx + x for x in random_idx[bucket_offset:]]
        else:
            full_bucket_size = (self.total_samples // self.micro_batch_size) \
                                * self.micro_batch_size
            full_bucket_offset = current_epoch_samples
            g = torch.Generator()
            g.manual_seed(self.epoch)
            idx_range_total = \
                torch.randperm(full_bucket_size, generator=g).tolist()
            idx_range_active = idx_range_total[full_bucket_offset:]
            idx_range = idx_range_active[self.data_parallel_rank::self.data_parallel_size]

        batch = []
        # Last batch if not complete will be dropped.
        for idx in idx_range:
            batch.append(idx)
            if len(batch) == self._global_batch_size_on_this_data_parallel_rank:
                print("*" * 4, self.data_parallel_rank, batch)
                self.consumed_samples += self._global_batch_size
                yield batch
                batch = []
        # Check the last partial batch and see drop_last is set
        if len(batch) > 0 and not self.drop_last:
            yield batch
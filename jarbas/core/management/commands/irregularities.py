import csv
import lzma
import os
from concurrent.futures import ThreadPoolExecutor

from bulk_update.helper import bulk_update

from jarbas.core.management.commands import LoadCommand
from jarbas.core.models import Reimbursement


class Command(LoadCommand):
    help = 'Load Serenata de Amor irregularities dataset'
    count = 0

    def add_arguments(self, parser):
        super().add_arguments(parser, add_drop_all=False)
        parser.add_argument(
            '--batch-size', '-b', dest='batch_size', type=int, default=4096,
            help='Batch size for bulk update (default: 4096)'
        )

    def handle(self, *args, **options):
        self.queue = []
        self.path = options['dataset']
        self.batch_size = options['batch_size']
        if not os.path.exists(self.path):
            raise FileNotFoundError(os.path.abspath(self.path))

        self.main()
        print('{:,} reimbursements updated.'.format(self.count))

    def irregularities(self):
        """Returns a Generator with bathces of irregularities."""
        print('Loading irregularities dataset…', end='\r')
        with lzma.open(self.path, mode='rt') as file_handler:
            batch = []
            for row in csv.DictReader(file_handler):
                batch.append(self.serialize(row))
                if len(batch) >= self.batch_size:
                    yield batch
                    batch = []
            yield batch

    def serialize(self, row):
        """
        Reads the dict generated by DictReader and returns another dict with
        the `document_id` and with data about the irregularities.
        """
        document_id = self.to_number(row.get('document_id'), cast=int)

        probability = None
        if 'probability' in row:
            probability = float(row['probability'])

        reserved_keys = (
            'applicant_id',
            'document_id',
            'probability',
            'year'
        )
        hypothesis = tuple(k for k in row.keys() if k not in reserved_keys)
        pairs = ((k, v) for k, v in row.items() if k in hypothesis)
        filtered = filter(lambda x: self.bool(x[1]), pairs)
        suspicions = dict((k, True) for k, v in filtered) or None

        return dict(
            document_id=document_id,
            probability=probability,
            suspicions=suspicions
        )

    def main(self):
        for batch in self.irregularities():
            with ThreadPoolExecutor(max_workers=32) as executor:
                executor.map(self.schedule_update, batch)
            self.update()

    def schedule_update(self, content):
        document_id = content.get('document_id')
        try:
            reimbursement = Reimbursement.objects.get(document_id=document_id)
        except Reimbursement.DoesNotExist:
            pass
        else:
            reimbursement.suspicions = content.get('suspicions')
            reimbursement.probability = content.get('probability')
            self.queue.append(reimbursement)

    def update(self):
        fields = ['probability', 'suspicions']
        bulk_update(self.queue, update_fields=fields)
        self.count += len(self.queue)
        print('{:,} reimbursements updated.'.format(self.count), end='\r')
        self.queue = []

    @staticmethod
    def bool(string):
        if string.lower() in ('false', '0', '0.0', 'none', 'nil', 'null'):
            string = ''
        return bool(string)

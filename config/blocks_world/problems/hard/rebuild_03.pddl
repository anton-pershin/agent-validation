(define (problem rebuild-03)
  (:domain blocks-world)
  (:objects a b c d e - block)
  (:init
    (clear a) (hand-empty) (on a b) (on b e) (on c d) (on e c) (on-table d))
  (:goal (and (on a e) (on d a) (on e b) (on-table b) (on-table c)))
)
